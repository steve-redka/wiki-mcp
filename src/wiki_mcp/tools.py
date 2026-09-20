"""MCP tool implementations. Kept independent of the MCP SDK itself (plain
functions/models) so they're testable and reusable from the CLI too — server.py
is the only place that knows about `mcp`.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from pydantic import BaseModel

from wiki_mcp import registry
from wikibot.client import WikiClientError
from wikibot.diff import TemplateDiff, diff_params
from wikibot.links import LinkIssue, find_unresolved_links
from wikibot.parser import find_templates, get_params, set_params
from wikibot.validate import ValidationResult, validate_params
from wikibot.wikis import PublishMode


class ProposedEdit(BaseModel):
    wiki: str
    title: str
    template_name: str
    diff: TemplateDiff
    validation: ValidationResult
    link_issues: list[LinkIssue] = []

    @property
    def ok(self) -> bool:
        return self.validation.ok


class SubmitResult(BaseModel):
    published: bool
    reason: str


class RawEditDiff(BaseModel):
    wiki: str
    title: str
    diff: str
    changed: bool
    link_issues: list[LinkIssue] = []


def _publish_gate(wiki: str, confirm: bool) -> SubmitResult | None:
    """Shared dry_run/review/auto gating for anything that writes to a wiki.
    Returns a blocking SubmitResult if the write should not proceed, or None
    if the caller is clear to write.
    """
    config = registry.get_config(wiki)
    if config.publish_mode == PublishMode.DRY_RUN:
        return SubmitResult(published=False, reason="publish_mode is dry_run; nothing written")
    if config.publish_mode == PublishMode.REVIEW and not confirm:
        return SubmitResult(published=False, reason="publish_mode is review; call again with confirm=True")
    return None


def list_wikis() -> list[str]:
    return registry.list_wikis()


def get_guidelines(wiki: str) -> str:
    """Cached editing-guideline text for a wiki: harvested wiki guideline
    pages plus the user's own custom.md preferences (personal conventions
    the wiki's own guidelines don't state), combined by `wiki-mcp
    init`/`harvest`. Reads local disk, not the live wiki, so call this once
    per session rather than re-fetching guideline pages with get_page.
    """
    text = registry.get_guidelines_text(wiki)
    if not text:
        return (
            "No cached guidelines for this wiki. Run `wiki-mcp init` or "
            "`wiki-mcp harvest <wiki>` to fetch some, or add personal "
            "preferences to its guidelines/custom.md."
        )
    return text


def _check_links(wiki: str, wikitext: str) -> list[LinkIssue]:
    """Flag [[links]] in wikitext that don't resolve against the wiki's
    cached page/redirect index. Empty (not an error) when no index has been
    harvested for this wiki yet.
    """
    index = registry.get_page_index(wiki)
    if index is None:
        return []
    return find_unresolved_links(wikitext, index)


def get_page(wiki: str, title: str, section: str | int | None = None) -> str:
    """Fetch a page's wikitext. Pass section (an index from list_sections) to
    fetch just that section instead of the whole page — worth doing on large
    pages, since the whole-page text has to fit in a single tool response.
    """
    client = registry.get_client(wiki)
    return client.get_page(title, section=section).wikitext


def list_sections(wiki: str, title: str) -> list[dict]:
    """Section index/title/anchor for a page, so a large page's sections can
    be targeted individually (via get_page's/propose_raw_edit's/
    submit_raw_edit's section parameter) without ever fetching the full text.
    """
    client = registry.get_client(wiki)
    return client.list_sections(title)


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
    Also flags [[links]] in param values that don't resolve against the
    wiki's cached page/redirect index (see link_issues on the result) —
    advisory, not a validation failure, since a redlink can be intentional.
    """
    schemas = registry.get_schemas(wiki)
    schema = schemas.get(template_name)
    if schema is None:
        raise ValueError(f"No harvested schema for template {template_name!r} on wiki {wiki!r}")

    current = _current_params(wiki, title, template_name)
    diff = diff_params(template_name, current, params)
    validation = validate_params(schema, params)
    link_issues = _check_links(wiki, "\n".join(params.values()))
    return ProposedEdit(
        wiki=wiki, title=title, template_name=template_name, diff=diff, validation=validation, link_issues=link_issues
    )


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

    blocked = _publish_gate(wiki, confirm)
    if blocked is not None:
        return blocked

    client = registry.get_client(wiki)
    page = client.get_page(title)
    new_wikitext = set_params(page.wikitext, template_name, params)
    client.edit_page(title, new_wikitext, summary=summary, base_revid=page.revid)
    return SubmitResult(published=True, reason="edit submitted")


def propose_raw_edit(wiki: str, title: str, new_wikitext: str, section: str | int | None = None) -> RawEditDiff:
    """Preview a wikitext edit as a unified diff, without writing anything.
    Use this for changes propose_edit/submit_edit can't do: new sections,
    prose rewrites, anything outside a single template's params.

    Pass section (an index from list_sections) to scope both the read and
    the diff to just that section, so new_wikitext only needs to contain the
    changed section, not the whole page — this matters on large pages, since
    the full text has to fit in a single tool call/response either way.
    section="new" is for appending a brand new section: there's nothing to
    read yet, so the diff is shown against empty content.

    There's no schema to validate against here, unlike propose_edit, since
    this isn't scoped to one template; review the diff carefully before
    calling submit_raw_edit, especially outside auto publish_mode. [[Links]]
    in new_wikitext that don't resolve against the wiki's cached page index
    are flagged in link_issues (advisory, not blocking — a redlink can be
    intentional), each with fuzzy-matched suggestions for the real title.
    """
    if section == "new":
        current_text = ""
    else:
        client = registry.get_client(wiki)
        page = client.get_page_if_exists(title, section=section)
        current_text = page.wikitext if page is not None else ""
    diff_lines = difflib.unified_diff(
        current_text.splitlines(keepends=True),
        new_wikitext.splitlines(keepends=True),
        fromfile=f"{title} (current)",
        tofile=f"{title} (proposed)",
    )
    link_issues = _check_links(wiki, new_wikitext)
    return RawEditDiff(
        wiki=wiki, title=title, diff="".join(diff_lines), changed=current_text != new_wikitext, link_issues=link_issues
    )


def submit_raw_edit(
    wiki: str,
    title: str,
    new_wikitext: str,
    summary: str,
    *,
    section: str | int | None = None,
    section_title: str | None = None,
    confirm: bool = False,
) -> SubmitResult:
    """Writes a wikitext edit, gated by the same dry_run/review/auto
    publish_mode rules as submit_edit, but with no param-schema validation.
    See propose_raw_edit for what section (and section_title, for
    section="new") do.
    """
    proposed = propose_raw_edit(wiki, title, new_wikitext, section=section)
    if not proposed.changed:
        return SubmitResult(published=False, reason="No changes: proposed wikitext matches current page")

    blocked = _publish_gate(wiki, confirm)
    if blocked is not None:
        return blocked

    client = registry.get_client(wiki)
    base_revid = client.get_current_revid(title)
    client.edit_page(
        title, new_wikitext, summary=summary, base_revid=base_revid, section=section, section_title=section_title
    )
    return SubmitResult(published=True, reason="edit submitted")


def upload_file(
    wiki: str,
    filename: str,
    file_path: str,
    *,
    comment: str = "",
    ignore_warnings: bool = False,
    confirm: bool = False,
) -> SubmitResult:
    """Upload a local file (e.g. an icon or screenshot) to the wiki's File
    namespace. file_path is a path on the machine running this server, not
    the wiki — filename is the name it should have on the wiki.

    Gated by the same dry_run/review/auto publish_mode rules as submit_edit.
    """
    blocked = _publish_gate(wiki, confirm)
    if blocked is not None:
        return blocked

    path = Path(file_path)
    if not path.is_file():
        return SubmitResult(published=False, reason=f"No such local file: {file_path!r}")

    client = registry.get_client(wiki)
    try:
        client.upload_file(filename, path.read_bytes(), comment=comment, ignore_warnings=ignore_warnings)
    except WikiClientError as e:
        return SubmitResult(published=False, reason=str(e))
    return SubmitResult(published=True, reason="file uploaded")


def search_pages(wiki: str, query: str, limit: int = 10) -> list[str]:
    client = registry.get_client(wiki)
    payload = client.call(
        {"action": "query", "list": "search", "srsearch": query, "srlimit": limit}
    )
    return [result["title"] for result in payload["query"]["search"]]
