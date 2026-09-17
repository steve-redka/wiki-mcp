"""MCP server exposing wiki editing as tools for an agent to drive.

Run with: wiki-mcp serve   (see wiki_mcp_cli.main)
or directly: python -m wiki_mcp.server
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv
from mcp.server.mcpserver import MCPServer

from wiki_mcp import tools

# WIKI_MCP_ENV_FILE, if set, points at a .env file by absolute path — needed
# when this server is launched by an MCP client from an arbitrary working
# directory (e.g. `claude mcp add`), since find_dotenv(usecwd=True) only
# works when the launch cwd happens to be the project directory. Loading one
# file (rather than one -e flag per wiki's credential) also means adding a
# new wiki's config doesn't require re-registering the server.
_env_override = os.environ.get("WIKI_MCP_ENV_FILE")
load_dotenv(_env_override if _env_override else find_dotenv(usecwd=True))

mcp = MCPServer("wiki-mcp")


@mcp.tool()
def list_wikis() -> list[str]:
    """List the wikis this server is configured for (by name, e.g. 'oldworldblues')."""
    return tools.list_wikis()


@mcp.tool()
def get_page(wiki: str, title: str) -> str:
    """Fetch a page's current wikitext."""
    return tools.get_page(wiki, title)


@mcp.tool()
def get_template_schema(wiki: str, template_name: str) -> dict:
    """Fetch a template's harvested parameter schema — check this before
    proposing an edit, since params not in this schema will be rejected.
    """
    return tools.get_template_schema(wiki, template_name)


@mcp.tool()
def propose_edit(wiki: str, title: str, template_name: str, params: dict[str, str]) -> dict:
    """Compute a diff and validate proposed template params, without writing
    anything. Always call this before submit_edit.
    """
    return tools.propose_edit(wiki, title, template_name, params).model_dump()


@mcp.tool()
def submit_edit(
    wiki: str,
    title: str,
    template_name: str,
    params: dict[str, str],
    summary: str,
    confirm: bool = False,
) -> dict:
    """Write an edit. Gated by the wiki's publish_mode (dry_run/review/auto)
    and by schema validation — see tools.submit_edit for the exact rules.
    """
    return tools.submit_edit(wiki, title, template_name, params, summary, confirm=confirm).model_dump()


@mcp.tool()
def upload_file(
    wiki: str,
    filename: str,
    file_path: str,
    comment: str = "",
    ignore_warnings: bool = False,
    confirm: bool = False,
) -> dict:
    """Upload a local file (e.g. an icon or screenshot) to the wiki's File
    namespace. file_path is a path on the machine running this server, not
    the wiki. Gated by the same publish_mode rules as submit_edit.
    """
    return tools.upload_file(
        wiki, filename, file_path, comment=comment, ignore_warnings=ignore_warnings, confirm=confirm
    ).model_dump()


@mcp.tool()
def search_pages(wiki: str, query: str, limit: int = 10) -> list[str]:
    """Search a wiki for pages matching a query."""
    return tools.search_pages(wiki, query, limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
