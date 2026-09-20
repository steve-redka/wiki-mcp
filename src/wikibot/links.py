"""Wiki page/redirect index: harvested once via the MediaWiki API and cached
locally, so a proposed edit's [[links]] can be checked against real page
titles without a live API call per link.

This is what catches an agent writing [[Rio Grande Republic]] when the real
page is Republic of the Rio Grande: the index has every real title and
redirect alias, so a target that matches neither gets flagged, with a
fuzzy-matched suggestion.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import mwparserfromhell
from pydantic import BaseModel

from wikibot.client import WikiClient

# Small words ignored when scoring suggestions by word overlap, so e.g.
# "Rio Grande Republic" still strongly matches "Republic of the Rio Grande"
# despite the word reordering that plain character-diff similarity misses.
_STOPWORDS = frozenset({"of", "the", "a", "an", "and"})

# Links into these namespaces aren't checked against the page index: they're
# either not content pages (File/Category/Template/...) or, for
# File/Category, a bare [[File:x]]/[[Category:x]] link embeds/tags rather
# than navigates, so a "broken" one isn't the same kind of mistake as a
# hallucinated article title.
NON_CONTENT_LINK_PREFIXES = (
    "file:", "image:", "category:", "template:", "special:", "media:",
    "help:", "user:", "talk:", "mediawiki:", "module:",
)


class PageIndex(BaseModel):
    titles: list[str]
    redirects: dict[str, str]  # alias title -> canonical target title


class LinkIssue(BaseModel):
    target: str
    suggestions: list[str]


class LinkResolver:
    """Normalized lookups built once from a PageIndex, so checking every
    link in a proposed edit doesn't redo an O(titles) normalization pass
    per link.
    """

    def __init__(self, index: PageIndex):
        self._by_title = {_normalize(t): t for t in index.titles}
        self._by_alias = {_normalize(a): t for a, t in index.redirects.items()}
        self._all_titles = index.titles + list(index.redirects)

    def resolve(self, target: str) -> str | None:
        """Canonical title for a link target (following one redirect hop),
        or None if it matches no known page or redirect alias.
        """
        normalized = _normalize(target)
        return self._by_title.get(normalized) or self._by_alias.get(normalized)

    def suggest(self, target: str, n: int = 3) -> list[str]:
        """Best-guess real titles for a target that didn't resolve, ranked
        by whichever of two signals is stronger: word overlap (catches a
        reordered/reworded title like "Rio Grande Republic" for the real
        "Republic of the Rio Grande") or character-sequence similarity
        (catches typos and near-misses word overlap wouldn't).
        """
        target_words = _significant_words(target)
        scored: list[tuple[float, str]] = []
        for candidate in self._all_titles:
            overlap = _word_overlap(target_words, _significant_words(candidate))
            ratio = difflib.SequenceMatcher(None, target.lower(), candidate.lower()).ratio()
            score = max(overlap, ratio)
            if score >= 0.4:
                scored.append((score, candidate))
        scored.sort(key=lambda pair: -pair[0])
        return [candidate for _, candidate in scored[:n]]


def _significant_words(title: str) -> set[str]:
    return {w for w in title.lower().split() if w not in _STOPWORDS}


def _word_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize(title: str) -> str:
    """MediaWiki treats link targets as case-insensitive on the first
    character and underscore/space-interchangeable; this normalizes both so
    comparisons match what the wiki itself would resolve.
    """
    title = title.split("#", 1)[0].strip().replace("_", " ")
    return title[:1].upper() + title[1:] if title else title


def harvest_page_index(client: WikiClient, namespaces: list[int] | None = None) -> PageIndex:
    """Batch-fetch every page title and redirect alias in the given
    namespaces (mainspace/0 by default), paginating through the whole
    namespace rather than one API call per page — this is the "sitemap" a
    link gets checked against, harvested once at init/harvest time instead
    of a live lookup per link.

    Two list=allpages passes per namespace (nonredirects, then redirects)
    plus a batched titles+redirects=1 lookup to resolve what each redirect
    points at: MediaWiki rejects redirects=1 combined with
    generator=allpages ("Use apfilterredir=nonredirects instead"), so
    resolving redirect targets has to be a separate, explicit-titles call.
    """
    namespaces = namespaces if namespaces is not None else [0]
    titles: set[str] = set()
    redirect_titles: set[str] = set()
    for ns in namespaces:
        titles |= _list_allpages(client, ns, filterredir="nonredirects")
        redirect_titles |= _list_allpages(client, ns, filterredir="redirects")

    redirects: dict[str, str] = {}
    sorted_redirect_titles = sorted(redirect_titles)
    batch_size = 50
    for i in range(0, len(sorted_redirect_titles), batch_size):
        batch = sorted_redirect_titles[i : i + batch_size]
        payload = client.call({"action": "query", "titles": "|".join(batch), "redirects": 1})
        for redirect in payload.get("query", {}).get("redirects", []):
            redirects[redirect["from"]] = redirect["to"]

    return PageIndex(titles=sorted(titles), redirects=redirects)


def _list_allpages(client: WikiClient, namespace: int, *, filterredir: str) -> set[str]:
    titles: set[str] = set()
    apcontinue = None
    while True:
        params = {
            "action": "query",
            "list": "allpages",
            "apnamespace": namespace,
            "apfilterredir": filterredir,
            "aplimit": 500,
        }
        if apcontinue:
            params["apcontinue"] = apcontinue
        payload = client.call(params)
        for page in payload.get("query", {}).get("allpages", []):
            titles.add(page["title"])
        apcontinue = payload.get("continue", {}).get("apcontinue")
        if not apcontinue:
            break
    return titles


def save_page_index(index: PageIndex, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(index.model_dump_json(indent=2))


def load_page_index(path: str | Path) -> PageIndex | None:
    path = Path(path)
    if not path.exists():
        return None
    return PageIndex.model_validate_json(path.read_text())


def find_unresolved_links(wikitext: str, index: PageIndex) -> list[LinkIssue]:
    """Internal [[...]] link targets that don't resolve against the cached
    page index, each with fuzzy-matched suggestions.

    Advisory, not a hard gate: a link to a page that genuinely doesn't exist
    yet (a redlink) is normal wiki practice, and this can't tell that apart
    from a hallucinated title — it's here for a reviewer to eyeball
    alongside the rest of the diff, the same way propose_raw_edit already
    has no schema check to lean on.
    """
    resolver = LinkResolver(index)
    code = mwparserfromhell.parse(wikitext)
    issues: list[LinkIssue] = []
    seen: set[str] = set()
    for link in code.filter_wikilinks():
        target = str(link.title).strip().lstrip(":").strip()
        if not target or target.startswith("#") or target in seen:
            continue
        seen.add(target)
        if target.lower().startswith(NON_CONTENT_LINK_PREFIXES):
            continue
        if resolver.resolve(target) is None:
            issues.append(LinkIssue(target=target, suggestions=resolver.suggest(target)))
    return issues
