"""Editing guideline text: harvesting a wiki's own guideline pages, caching
them locally, and merging them with the user's own custom preferences into
one block of reference text.

The cache exists so an agent reading guidelines doesn't cost a live API
round trip (or N of them, one per guideline page) on every call — harvesting
happens once, at `wiki-mcp init`/`harvest` time, and get_guidelines just
reads local disk after that.
"""

from __future__ import annotations

import json
from pathlib import Path

from wikibot.client import WikiClient

HARVESTED_FILENAME = "harvested.json"
CONDENSED_FILENAME = "condensed.json"
CUSTOM_FILENAME = "custom.md"

CUSTOM_GUIDELINES_TEMPLATE = """\
<!-- Your own editing preferences for this wiki, on top of whatever the
wiki's guideline pages say below. Free text, loaded alongside them every
time get_guidelines is called.

Example: don't gloss in-universe abbreviations in prose (e.g. don't write
"[[Republic of the Rio Grande]] (RRG)") unless the wiki's own articles do —
that reads like a developer/data artifact, not something an in-universe
article would say.
-->
"""


def harvest_guideline_pages(client: WikiClient, titles: list[str]) -> dict[str, str]:
    """Fetch wikitext for each guideline page. A title that doesn't exist
    (or was mistyped in config) is silently skipped, not an error.
    """
    pages: dict[str, str] = {}
    for title in titles:
        page = client.get_page_if_exists(title)
        if page is not None:
            pages[title] = page.wikitext
    return pages


def save_guideline_pages(pages: dict[str, str], directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / HARVESTED_FILENAME
    path.write_text(json.dumps(pages, indent=2, sort_keys=True))
    return path


def load_guideline_pages(directory: str | Path) -> dict[str, str]:
    path = Path(directory) / HARVESTED_FILENAME
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_condensed_pages(pages: dict[str, str], directory: str | Path) -> Path:
    """Save hand/agent-condensed versions of harvested pages, keyed by the
    same titles as harvested.json. Nothing in this codebase generates these
    automatically — a raw wiki guideline page is usually as much account
    setup and Discord etiquette as it is actual editing rules, and telling
    those apart is a job for whoever (or whatever agent) reads the page, not
    a fixed heuristic. Run `wiki-mcp harvest`, then ask your agent to read
    harvested.json and write the condensed rules here.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONDENSED_FILENAME
    path.write_text(json.dumps(pages, indent=2, sort_keys=True))
    return path


def load_condensed_pages(directory: str | Path) -> dict[str, str]:
    path = Path(directory) / CONDENSED_FILENAME
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def ensure_custom_guidelines_file(directory: str | Path) -> Path:
    """Create a starter custom.md for personal preferences if one doesn't
    exist yet. Never overwrites an existing file, so re-running `harvest`
    doesn't clobber what the user wrote in it.
    """
    directory = Path(directory)
    path = directory / CUSTOM_FILENAME
    if not path.exists():
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(CUSTOM_GUIDELINES_TEMPLATE)
    return path


def load_guidelines_text(directory: str | Path) -> str:
    """Combine cached harvested guideline pages with custom.md into one
    block of reference text. Pure local read, no network call.

    A page with a condensed.json entry (see save_condensed_pages) uses that
    instead of its raw harvested wikitext, so an agent reading guidelines
    isn't handed a full wiki page's worth of account setup and tooling
    instructions along with the handful of rules that actually matter.
    """
    directory = Path(directory)
    sections: list[str] = []
    condensed = load_condensed_pages(directory)

    for title, wikitext in sorted(load_guideline_pages(directory).items()):
        sections.append(f"== {title} ==\n{condensed.get(title, wikitext)}")

    custom_path = directory / CUSTOM_FILENAME
    if custom_path.exists():
        custom_text = custom_path.read_text().strip()
        if custom_text and custom_text != CUSTOM_GUIDELINES_TEMPLATE.strip():
            sections.append(f"== Your personal preferences ==\n{custom_text}")

    return "\n\n".join(sections)
