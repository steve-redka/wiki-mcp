"""`wiki-mcp` CLI: `init` bootstraps a new wiki's config, `harvest` re-runs
template schema / guideline discovery for an already-configured wiki, `serve`
runs the MCP server. See PLAN.md for the full init flow this implements.
"""

from __future__ import annotations

import sys
from pathlib import Path

import mwparserfromhell
import typer
import yaml
from dotenv import find_dotenv, load_dotenv

from wikibot import guidelines as guidelines_lib
from wikibot.client import WikiClient, WikiClientError
from wikibot.links import harvest_page_index, save_page_index
from wikibot.schema import (
    TemplateSchema,
    find_pages_using_template,
    harvest_by_sampling,
    harvest_from_doc,
    harvest_templatedata,
    save_schema,
)
from wikibot.wikis import PublishMode, WikiConfig, load_wiki_config

load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(add_completion=False)

CANDIDATE_GUIDELINE_PAGES = [
    "Project:Manual of Style",
    "Project:Bot policy",
    "Project:Bots",
    "Help:Editing",
]

# Terms to full-text search for guideline pages that don't match any of the
# fixed candidate titles above — wikis often name theirs something wiki-
# specific, like "New Editor's Guide to OWB Wiki Editing". Search, not exact
# title match, so results are suggestions for the user to confirm rather
# than auto-included.
GUIDELINE_SEARCH_TERMS = [
    "editing guide",
    "editing guidelines",
    "style guide",
    "new editor",
]

# Mainspace, Project, and Help — where guideline pages conventionally live.
GUIDELINE_SEARCH_NAMESPACES = "0|4|12"

FULL_HARVEST_THRESHOLD = 200


@app.command()
def init(
    url: str = typer.Argument(..., help="Base wiki URL, e.g. https://oldworldblues.wiki.gg"),
    name: str = typer.Option(None, help="Short name for this wiki's config (defaults to the hostname)"),
) -> None:
    """Bootstrap config for a new wiki: credentials, template schemas, guidelines."""
    wiki_name = name or url.split("//")[-1].split(".")[0]
    typer.echo(f"Setting up {wiki_name!r} ({url})")

    # 1. Site discovery
    probe_config = WikiConfig(name=wiki_name, site=url, bot_username="unused", secrets_ref="env:unused")
    probe = WikiClient(probe_config)
    siteinfo = probe.call({"action": "query", "meta": "siteinfo", "siprop": "general|extensions"})
    general = siteinfo["query"]["general"]
    extensions = {e["name"] for e in siteinfo["query"].get("extensions", [])}
    has_templatedata = "TemplateData" in extensions
    typer.echo(f"  Found MediaWiki {general.get('generator')}; TemplateData extension: {has_templatedata}")

    # 2. Credentials
    env_var = f"{wiki_name.upper()}_BOT_PASSWORD"
    bot_username = typer.prompt(
        "Bot username — MainAccountUsername@BotPasswordName, e.g. 'ProfaneServitor@Bot' "
        "(the name of the entry on Special:BotPasswords, not your account name alone)"
    )
    bot_password = typer.prompt("Bot password", hide_input=True)

    config = WikiConfig(name=wiki_name, site=url, bot_username=bot_username, secrets_ref=f"env:{env_var}")
    client = WikiClient(config)
    client.login(username=bot_username, password=bot_password)
    typer.echo("  Login OK")
    _check_upload_permission(client)

    _write_secret(env_var, bot_password)
    typer.echo("  Wrote credentials to .env (make sure it's gitignored)")

    # 3-4. Template schemas
    schema_dir = Path("config/wikis") / wiki_name / config.template_schema_dir
    _harvest_templates(client, has_templatedata=has_templatedata, schema_dir=schema_dir)

    # 4b. Page/redirect index, for catching [[links]] to titles that don't exist
    _harvest_page_index_interactive(client, Path("config/wikis") / wiki_name / "pages.json")

    # 5. Guidelines
    found_guidelines = _discover_guidelines(client)
    typer.echo(f"  Found guideline pages: {found_guidelines or '(none of the common ones)'}")
    extra = typer.prompt(
        "Any other guideline page titles to include? (comma-separated, blank to skip)",
        default=_suggest_guidelines_prompt(client, found_guidelines),
    )
    found_guidelines += [t.strip() for t in extra.split(",") if t.strip()]
    _harvest_and_cache_guidelines(
        client, found_guidelines, Path("config/wikis") / wiki_name / config.guidelines_dir
    )

    # 6. Write config + summary
    config_full = WikiConfig(
        name=wiki_name,
        site=url,
        bot_username=bot_username,
        secrets_ref=f"env:{env_var}",
        publish_mode=PublishMode.REVIEW,
        guideline_pages=found_guidelines,
    )
    config_path = Path("config/wikis") / f"{wiki_name}.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(config_full.model_dump(mode="json"), sort_keys=False))
    typer.echo(f"\nWrote {config_path}. Review it before running anything against the live wiki.")
    _print_mcp_add_hint()


@app.command()
def harvest(
    wiki: str = typer.Argument(..., help="Wiki name, matching config/wikis/<wiki>.yaml"),
) -> None:
    """Re-run template schema and guideline discovery for an already-configured
    wiki, reusing its existing credentials instead of re-prompting for them.
    """
    config_path = Path("config/wikis") / f"{wiki}.yaml"
    if not config_path.exists():
        typer.echo(f"No config at {config_path} — run `wiki-mcp init <url>` first.")
        raise typer.Exit(1)

    config = load_wiki_config(config_path)
    client = WikiClient(config)
    client.login()
    typer.echo(f"Logged in to {config.site}")
    _check_upload_permission(client)

    siteinfo = client.call({"action": "query", "meta": "siteinfo", "siprop": "extensions"})
    has_templatedata = "TemplateData" in {e["name"] for e in siteinfo["query"].get("extensions", [])}

    schema_dir = config_path.parent / wiki / config.template_schema_dir
    _harvest_templates(client, has_templatedata=has_templatedata, schema_dir=schema_dir)

    _harvest_page_index_interactive(client, config_path.parent / wiki / "pages.json")

    discovered = _discover_guidelines(client)
    typer.echo(f"  Found guideline pages: {discovered or '(none of the common ones)'}")
    extra = typer.prompt(
        "Any other guideline page titles to include? (comma-separated, blank to skip)",
        default=_suggest_guidelines_prompt(client, discovered),
    )
    discovered += [t.strip() for t in extra.split(",") if t.strip()]

    merged_guidelines = sorted(set(config.guideline_pages) | set(discovered))
    if merged_guidelines != config.guideline_pages:
        updated = config.model_copy(update={"guideline_pages": merged_guidelines})
        config_path.write_text(yaml.safe_dump(updated.model_dump(mode="json"), sort_keys=False))
        typer.echo(f"  Updated {config_path} with {len(merged_guidelines)} guideline page(s)")

    _harvest_and_cache_guidelines(client, merged_guidelines, config_path.parent / wiki / config.guidelines_dir)

    _print_mcp_add_hint()


def _harvest_templates(client: WikiClient, *, has_templatedata: bool, schema_dir: Path) -> None:
    """Prompt for scope, harvest via TemplateData then the /doc fallback, save
    results to schema_dir. Shared by `init` and `harvest`.
    """
    allpages = client.call({"action": "query", "list": "allpages", "apnamespace": 10, "aplimit": 500})
    template_titles = [p["title"].removeprefix("Template:") for p in allpages["query"]["allpages"]]
    truncated = "continue" in allpages
    count_display = f"{len(template_titles)}+" if truncated else str(len(template_titles))
    typer.echo(f"  Found {count_display} templates in the Template namespace")

    large_wiki = len(template_titles) > FULL_HARVEST_THRESHOLD or truncated
    if large_wiki:
        scoped = typer.confirm(
            "That's a lot — likely mostly unrelated to infoboxes. Harvest scoped to sample pages instead?",
            default=True,
        )
    else:
        scoped = not typer.confirm(f"Harvest schemas for all {count_display} templates?", default=True)

    if scoped:
        sample_input = typer.prompt("Sample page titles (comma-separated, e.g. 'Timekeepers')")
        sample_titles = [t.strip() for t in sample_input.split(",") if t.strip()]
        harvest_titles = _templates_used_on(client, sample_titles)
    else:
        sample_titles = []
        harvest_titles = template_titles

    schemas: dict[str, TemplateSchema] = {}
    if has_templatedata and harvest_titles:
        schemas = harvest_templatedata(client, harvest_titles)
        typer.echo(f"  Harvested {len(schemas)} template schemas via TemplateData")

    unresolved = [t for t in harvest_titles if t not in schemas]
    doc_harvested = 0
    for title in unresolved:
        schema = harvest_from_doc(client, title)
        if schema is not None:
            schemas[title] = schema
            doc_harvested += 1
    if doc_harvested:
        typer.echo(f"  Harvested {doc_harvested} more template schemas from /doc pages")

    unresolved = [t for t in harvest_titles if t not in schemas]
    sampled = 0
    for title in unresolved:
        candidates = sample_titles or find_pages_using_template(client, title, limit=5)
        schema = harvest_by_sampling(client, title, candidates)
        if schema is not None:
            schemas[title] = schema
            sampled += 1
    if sampled:
        typer.echo(f"  Inferred {sampled} more template schema(s) from real usage (no docs/TemplateData)")

    for schema in schemas.values():
        save_schema(schema, schema_dir)

    still_unresolved = [t for t in harvest_titles if t not in schemas]
    if still_unresolved:
        typer.echo(f"  {len(still_unresolved)} templates couldn't be resolved by any method: {sorted(still_unresolved)}")


def _check_upload_permission(client: WikiClient) -> None:
    rights = client.get_user_rights()
    if "upload" in rights:
        typer.echo("  Bot password has upload permission")
    else:
        typer.echo(
            "  Bot password does NOT have upload permission "
            "(add the 'Upload new files' grant on Special:BotPasswords if you need upload_file to work)"
        )


def _discover_guidelines(client: WikiClient) -> list[str]:
    found: list[str] = []
    for title in CANDIDATE_GUIDELINE_PAGES:
        try:
            client.get_page(title)
            found.append(title)
        except WikiClientError:
            continue
    return found


def _search_guideline_candidates(client: WikiClient, exclude: list[str]) -> list[str]:
    """Full-text search the wiki for likely guideline pages beyond
    CANDIDATE_GUIDELINE_PAGES's fixed titles, since a wiki's own guide is
    often named something wiki-specific that no exact-title guess would
    catch (e.g. "New Editor's Guide to OWB Wiki Editing"). Best-effort: a
    search backend hiccup shouldn't break init/harvest, so failures are
    swallowed per term.
    """
    excluded = {t.lower() for t in exclude}
    seen: set[str] = set()
    candidates: list[str] = []
    for term in GUIDELINE_SEARCH_TERMS:
        try:
            payload = client.call(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": term,
                    "srnamespace": GUIDELINE_SEARCH_NAMESPACES,
                    "srlimit": 5,
                }
            )
        except WikiClientError:
            continue
        for result in payload.get("query", {}).get("search", []):
            title = result["title"]
            key = title.lower()
            if key in excluded or key in seen:
                continue
            seen.add(key)
            candidates.append(title)
    return candidates


def _suggest_guidelines_prompt(client: WikiClient, exclude: list[str]) -> str:
    """Fuzzy-search for guideline pages the exact-title check missed, echo
    what turned up, and return them as a pre-filled default for the "any
    other titles" prompt so accepting them is just pressing enter.
    """
    suggested = _search_guideline_candidates(client, exclude)
    if suggested:
        typer.echo(f"  Fuzzy search also turned up possible guideline pages: {suggested}")
    return ", ".join(suggested)


def _harvest_and_cache_guidelines(client: WikiClient, titles: list[str], directory: Path) -> None:
    """Fetch each guideline page's wikitext and cache it locally, so
    get_guidelines is a local read instead of a live fetch per page. Also
    makes sure a starter custom.md exists for the user's own preferences.
    """
    pages = guidelines_lib.harvest_guideline_pages(client, titles)
    guidelines_lib.save_guideline_pages(pages, directory)
    custom_path = guidelines_lib.ensure_custom_guidelines_file(directory)
    typer.echo(f"  Cached {len(pages)} guideline page(s) locally; add personal preferences to {custom_path}")


def _harvest_page_index_interactive(client: WikiClient, index_path: Path) -> None:
    """Cache the wiki's mainspace page/redirect titles locally so
    propose_edit/propose_raw_edit can flag [[links]] that don't resolve to a
    real page — one batch of paginated calls instead of a live lookup per
    link. Prompted (default yes) since on a very large wiki it can mean a
    fair number of requests.
    """
    do_it = typer.confirm(
        "Harvest the page/redirect index too? Lets propose_edit/propose_raw_edit "
        "flag [[links]] to titles that don't actually exist.",
        default=True,
    )
    if not do_it:
        return
    typer.echo("  Fetching page/redirect index (this can take a while on a large wiki)...")
    index = harvest_page_index(client)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    save_page_index(index, index_path)
    typer.echo(f"  Cached {len(index.titles)} page title(s) and {len(index.redirects)} redirect(s)")


def _write_secret(env_var: str, value: str) -> None:
    """Idempotently set env_var=value in .env, replacing any existing line
    for that variable instead of appending a duplicate.
    """
    secrets_path = Path(".env")
    existing_lines = secrets_path.read_text().splitlines() if secrets_path.exists() else []
    kept_lines = [line for line in existing_lines if not line.startswith(f"{env_var}=")]
    kept_lines.append(f"{env_var}={value}")
    secrets_path.write_text("\n".join(kept_lines) + "\n")


def _print_mcp_add_hint() -> None:
    """Print a ready-to-run `claude mcp add` command, so wiring this wiki's
    tools up to an MCP client doesn't require hand-assembling absolute paths.

    One server registration covers every wiki configured under config/wikis/
    (WIKI_MCP_CONFIG_ROOT points at the directory, not this one wiki, and
    WIKI_MCP_ENV_FILE loads every credential in .env) — so adding a second
    wiki later doesn't require re-registering with more -e flags. The secret
    itself is never printed here; only the .env file's path is.
    """
    config_root = Path("config/wikis").resolve()
    env_path = Path(".env").resolve()
    wiki_mcp_bin = Path(sys.argv[0]).resolve()
    typer.echo(
        "\nTo make wiki-mcp's tools available to an MCP client (e.g. Claude Code), run:\n"
        "  claude mcp add wiki-mcp \\\n"
        f"    -e WIKI_MCP_CONFIG_ROOT={config_root} \\\n"
        f"    -e WIKI_MCP_ENV_FILE={env_path} \\\n"
        f"    -- {wiki_mcp_bin} serve\n"
        "(skip if already registered — this works for every wiki under config/wikis/, "
        "not just the one you just set up)"
    )


def _templates_used_on(client: WikiClient, titles: list[str]) -> list[str]:
    names: set[str] = set()
    for title in titles:
        page = client.get_page(title)
        code = mwparserfromhell.parse(page.wikitext)
        names.update(str(t.name).strip() for t in code.filter_templates())
    return sorted(names)


@app.command()
def serve() -> None:
    """Run the MCP server (stdio transport) for the configured wikis."""
    from wiki_mcp.server import main as server_main

    server_main()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
