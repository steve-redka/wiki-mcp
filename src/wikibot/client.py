"""Thin MediaWiki API session: auth, page fetch, page edit.

Targets the generic MediaWiki API (action=...), so it works against any
MediaWiki install — wiki.gg, Fandom, self-hosted — not just one site.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from wikibot.wikis import WikiConfig

USER_AGENT = "wiki-mcp/0.1 (https://github.com/; automated wiki editing tool)"


class WikiClientError(RuntimeError):
    pass


@dataclass
class Page:
    title: str
    pageid: int
    wikitext: str
    revid: int


class WikiClient:
    """A logged-in session against one wiki's MediaWiki API."""

    def __init__(self, config: WikiConfig, *, min_request_interval: float = 1.0):
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._min_request_interval = min_request_interval
        self._last_request_time = 0.0
        self._logged_in = False

    # -- low-level ---------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)

    def _request(self, method: str, params: dict, *, files: dict | None = None) -> dict:
        self._throttle()
        params = {**params, "format": "json"}
        if files:
            response = self.session.post(self.config.api_url, data=params, files=files, timeout=60)
        else:
            response = self.session.request(
                method, self.config.api_url, params=params if method == "GET" else None,
                data=params if method == "POST" else None, timeout=30,
            )
        self._last_request_time = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if "error" in payload:
            raise WikiClientError(f"MediaWiki API error: {payload['error']}")
        return payload

    def _get(self, params: dict) -> dict:
        return self._request("GET", params)

    def _post(self, params: dict) -> dict:
        return self._request("POST", params)

    def call(self, params: dict, *, method: str = "GET") -> dict:
        """Escape hatch for API actions not wrapped by a dedicated method
        (e.g. action=templatedata). Returns the raw decoded JSON response.
        """
        return self._request(method, params)

    def _get_token(self, token_type: str = "csrf") -> str:
        payload = self._get({"action": "query", "meta": "tokens", "type": token_type})
        return payload["query"]["tokens"][f"{token_type}token"]

    # -- auth ----------------------------------------------------------

    def login(self, username: str | None = None, password: str | None = None) -> None:
        """Log in with a Special:BotPasswords username/password pair."""
        username = username or self.config.bot_username
        password = password if password is not None else self.config.resolve_secret()
        login_token = self._get_token("login")
        payload = self._post(
            {
                "action": "login",
                "lgname": username,
                "lgpassword": password,
                "lgtoken": login_token,
            }
        )
        login_info = payload.get("login", {})
        result = login_info.get("result")
        if result != "Success":
            reason = login_info.get("reason", {})
            reason_text = reason.get("text") if isinstance(reason, dict) else reason
            hint = ""
            if result == "Failed" and "@" not in username:
                hint = (
                    " (a Bot Password username must be 'MainAccountUsername@BotPasswordName' "
                    "— did you pass the bot password entry's name alone?)"
                )
            detail = f": {reason_text}" if reason_text else ""
            raise WikiClientError(f"Login failed: {result}{detail}{hint}")
        self._logged_in = True

    def get_user_rights(self) -> list[str]:
        """Effective rights for the current login, including whatever the
        Bot Password's grants restrict them to (not just the underlying
        account's full rights) — used to check upload permission upfront
        rather than finding out only after an upload fails.
        """
        payload = self._get({"action": "query", "meta": "userinfo", "uiprop": "rights"})
        return payload["query"]["userinfo"].get("rights", [])

    # -- pages -----------------------------------------------------------

    def get_page(self, title: str, *, section: str | int | None = None) -> Page:
        """Fetch a page's wikitext. Pass section (a numeric index from
        list_sections) to fetch just that section's content instead of the
        whole page — the returned revid is always the whole page's current
        revision, since MediaWiki has no separate per-section revisions.
        """
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "ids|content",
            "rvslots": "main",
            "titles": title,
        }
        if section is not None:
            params["rvsection"] = str(section)
        payload = self._get(params)
        pages = payload["query"]["pages"]
        (page,) = pages.values()
        if "missing" in page:
            raise WikiClientError(f"Page not found: {title!r}")
        revision = page["revisions"][0]
        wikitext = revision["slots"]["main"]["*"]
        return Page(title=page["title"], pageid=page["pageid"], wikitext=wikitext, revid=revision["revid"])

    def get_page_if_exists(self, title: str, *, section: str | int | None = None) -> Page | None:
        try:
            return self.get_page(title, section=section)
        except WikiClientError:
            return None

    def get_current_revid(self, title: str) -> int | None:
        """Just the current revision id, with no content fetched at all —
        for when a caller (e.g. a section-scoped edit) needs base_revid for
        conflict-safety without pulling the whole page across the wire.
        """
        payload = self._get({"action": "query", "prop": "revisions", "rvprop": "ids", "titles": title})
        (page,) = payload["query"]["pages"].values()
        if "missing" in page:
            return None
        return page["revisions"][0]["revid"]

    def list_sections(self, title: str) -> list[dict]:
        """Section index/title/anchor for a page, via action=parse&prop=sections
        — lets a caller find which section to target without ever fetching
        the page's full wikitext.
        """
        payload = self._get({"action": "parse", "page": title, "prop": "sections"})
        return [
            {"index": s["index"], "level": s["level"], "line": s["line"], "anchor": s["anchor"]}
            for s in payload["parse"]["sections"]
        ]

    def edit_page(
        self,
        title: str,
        new_wikitext: str,
        *,
        summary: str,
        base_revid: int | None,
        section: str | int | None = None,
        section_title: str | None = None,
    ) -> None:
        """Submit an edit. Pass the page's current revid as base_revid so
        MediaWiki rejects the edit on a concurrent change instead of silently
        clobbering it; pass None only to create a page that doesn't exist yet.

        Pass section (a numeric index from list_sections, or "new" to append
        a new section with section_title as its heading) to replace just that
        section's content instead of the whole page — new_wikitext only needs
        to contain that section, not the full page.
        """
        if not self._logged_in:
            raise WikiClientError("Must call login() before editing")
        csrf_token = self._get_token("csrf")
        params = {
            "action": "edit",
            "title": title,
            "text": new_wikitext,
            "summary": summary,
            "bot": True,
            "token": csrf_token,
        }
        if section is not None:
            params["section"] = str(section)
        if section_title is not None:
            params["sectiontitle"] = section_title
        if base_revid is not None:
            params["baserevid"] = base_revid
        else:
            params["createonly"] = True
        self._post(params)

    # -- files -----------------------------------------------------------

    def upload_file(self, filename: str, content: bytes, *, comment: str = "", ignore_warnings: bool = False) -> dict:
        """Upload a file (e.g. an icon or screenshot) to the wiki's File
        namespace. filename is the target name on the wiki, not a local path
        — content is the raw bytes to upload.
        """
        if not self._logged_in:
            raise WikiClientError("Must call login() before uploading")
        csrf_token = self._get_token("csrf")
        payload = self._request(
            "POST",
            {
                "action": "upload",
                "filename": filename,
                "comment": comment,
                "token": csrf_token,
                "ignorewarnings": "1" if ignore_warnings else "0",
            },
            files={"file": (filename, content)},
        )
        result = payload.get("upload", {})
        if result.get("result") == "Warning":
            raise WikiClientError(
                f"Upload warning (pass ignore_warnings=True to override): {result.get('warnings')}"
            )
        if result.get("result") != "Success":
            raise WikiClientError(f"Upload failed: {result}")
        return result
