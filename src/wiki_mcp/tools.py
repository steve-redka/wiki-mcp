"""MCP tool implementations. Kept independent of the MCP SDK itself (plain
functions/models) so they're testable and reusable from the CLI too — server.py
is the only place that knows about `mcp`.
"""

from __future__ import annotations

from pydantic import BaseModel

from wiki_mcp import registry
from wikibot.diff import TemplateDiff, diff_params
from wikibot.parser import find_templates, get_params, set_params
from wikibot.validate import ValidationResult, validate_params
from wikibot.wikis import PublishMode


class ProposedEdit(BaseModel):
    wiki: str
    title: str
    template_name: str
    diff: TemplateDiff
    validation: ValidationResult

    @property
    def ok(self) -> bool:
        return self.validation.ok


class SubmitResult(BaseModel):
    published: bool
    reason: str


def list_wikis() -> list[str]:
    return registry.list_wikis()


def get_page(wiki: str, title: str) -> str:
    client = registry.get_client(wiki)
    return client.get_page(title).wikitext


def get_template_schema(wiki: str, template_name: str) -> dict:
    schemas = registry.get_schemas(wiki)
    schema = schemas.get(template_name)
    if schema is None:
        raise ValueError(
            f"No harvested schema for template {template_name!r} on wiki {wiki!r}. "
            "Run `wiki-mcp init` (or re-run schema harvesting) before proposing edits with it."
        )
    return schema.model_dump()


def _current_params(wiki: str, title: str, template_name: str) -> dict[str, str]:
    client = registry.get_client(wiki)
    page = client.get_page(title)
    templates = find_templates(page.wikitext, template_name)
    if not templates:
        raise ValueError(f"Template {template_name!r} not found on page {title!r}")
    return get_params(templates[0])


def propose_edit(wiki: str, title: str, template_name: str, params: dict[str, str]) -> ProposedEdit:
    """Computes a diff and validates proposed params against the harvested
    schema. Never writes to the wiki — this is the "show me first" step.
    """
    schemas = registry.get_schemas(wiki)
    schema = schemas.get(template_name)
    if schema is None:
        raise ValueError(f"No harvested schema for template {template_name!r} on wiki {wiki!r}")

    current = _current_params(wiki, title, template_name)
    diff = diff_params(template_name, current, params)
    validation = validate_params(schema, params)
    return ProposedEdit(wiki=wiki, title=title, template_name=template_name, diff=diff, validation=validation)


def submit_edit(
    wiki: str,
    title: str,
    template_name: str,
    params: dict[str, str],
    summary: str,
    *,
    confirm: bool = False,
) -> SubmitResult:
    """Writes the edit, gated by the wiki's publish_mode:
      - dry_run: never writes, regardless of `confirm`.
      - review: writes only if `confirm=True` (the agent/human explicitly
        signed off on a prior propose_edit diff).
      - auto: writes as long as validation passes.
    Validation failures block the write in every mode.
    """
    proposed = propose_edit(wiki, title, template_name, params)
    if not proposed.ok:
        return SubmitResult(
            published=False,
            reason=f"Validation failed: unknown_params={proposed.validation.unknown_params}, "
            f"missing_required={proposed.validation.missing_required_params}",
        )
    if proposed.diff.is_empty:
        return SubmitResult(published=False, reason="No changes: proposed params match current page")

    config = registry.get_config(wiki)
    if config.publish_mode == PublishMode.DRY_RUN:
        return SubmitResult(published=False, reason="publish_mode is dry_run; edit not written")
    if config.publish_mode == PublishMode.REVIEW and not confirm:
        return SubmitResult(published=False, reason="publish_mode is review; call again with confirm=True")

    client = registry.get_client(wiki)
    page = client.get_page(title)
    new_wikitext = set_params(page.wikitext, template_name, params)
    client.edit_page(title, new_wikitext, summary=summary, base_revid=page.revid)
    return SubmitResult(published=True, reason="edit submitted")


def search_pages(wiki: str, query: str, limit: int = 10) -> list[str]:
    client = registry.get_client(wiki)
    payload = client.call(
        {"action": "query", "list": "search", "srsearch": query, "srlimit": limit}
    )
    return [result["title"] for result in payload["query"]["search"]]
