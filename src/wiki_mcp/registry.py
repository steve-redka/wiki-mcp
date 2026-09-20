"""Loads and caches per-wiki clients/schemas so MCP tools can address a wiki
by name (e.g. "oldworldblues") without re-reading config on every call.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from wikibot.client import WikiClient
from wikibot.guidelines import load_guidelines_text
from wikibot.links import PageIndex, load_page_index
from wikibot.schema import TemplateSchema, load_all_schemas
from wikibot.wikis import WikiConfig, config_dir_for, load_wiki_config


def _config_root() -> Path:
    """Where config/wikis/*.yaml live. Defaults to a path relative to the
    current working directory, but that only holds if this process happens to
    be launched from inside the project — set WIKI_MCP_CONFIG_ROOT to an
    absolute path for a reliable location (e.g. when this server is
    registered with an MCP client that may launch it from anywhere).
    """
    override = os.environ.get("WIKI_MCP_CONFIG_ROOT")
    return Path(override) if override else Path("config/wikis")


def _config_path(wiki: str) -> Path:
    path = _config_root() / f"{wiki}.yaml"
    if not path.exists():
        raise ValueError(f"No config found for wiki {wiki!r} at {path}")
    return path


@lru_cache(maxsize=None)
def get_config(wiki: str) -> WikiConfig:
    return load_wiki_config(_config_path(wiki))


@lru_cache(maxsize=None)
def get_client(wiki: str) -> WikiClient:
    config = get_config(wiki)
    client = WikiClient(config)
    client.login()
    return client


@lru_cache(maxsize=None)
def get_schemas(wiki: str) -> dict[str, TemplateSchema]:
    config = get_config(wiki)
    schema_dir = config_dir_for(_config_path(wiki)) / config.template_schema_dir
    return load_all_schemas(schema_dir)


@lru_cache(maxsize=None)
def get_guidelines_text(wiki: str) -> str:
    config = get_config(wiki)
    directory = config_dir_for(_config_path(wiki)) / config.guidelines_dir
    return load_guidelines_text(directory)


@lru_cache(maxsize=None)
def get_page_index(wiki: str) -> PageIndex | None:
    path = config_dir_for(_config_path(wiki)) / "pages.json"
    return load_page_index(path)


def list_wikis() -> list[str]:
    root = _config_root()
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.yaml"))
