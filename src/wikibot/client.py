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

    def _request(self, method: str, params: dict) -> dict:
        self._throttle()
        params = {**params, "format": "json"}
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

    # -- pages -----------------------------------------------------------

    def get_page(self, title: str) -> Page:
        payload = self._get(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "ids|content",
                "rvslots": "main",
                "titles": title,
            }
        )
        pages = payload["query"]["pages"]
        (page,) = pages.values()
        if "missing" in page:
            raise WikiClientError(f"Page not found: {title!r}")
        revision = page["revisions"][0]
        wikitext = revision["slots"]["main"]["*"]
        return Page(title=page["title"], pageid=page["pageid"], wikitext=wikitext, revid=revision["revid"])

    def get_page_if_exists(self, title: str) -> Page | None:
        try:
            return self.get_page(title)
        except WikiClientError:
            return None

    def edit_page(self, title: str, new_wikitext: str, *, summary: str, base_revid: int | None) -> None:
        """Submit an edit. Pass the page's current revid as base_revid so
        MediaWiki rejects the edit on a concurrent change instead of silently
        clobbering it; pass None only to create a page that doesn't exist yet.
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
        if base_revid is not None:
            params["baserevid"] = base_revid
        else:
            params["createonly"] = True
        self._post(params)
