# wiki-mcp

**wiki-mcp keeps game wiki pages accurate automatically**, instead of relying
on a volunteer to notice a page is outdated, look up the new numbers, and
hand-edit it correctly.

## The problem this solves

wiki-mcp is a tool fo automatic fandom wiki editing. Something else (a separate data-collection tool, not part of this project) gathers the up-to-date game data; wiki-mcp takes that data and applies it to the wiki page, safely. Or you can just tell it to do other edits from a chat.

## How it works, in plain terms

1. wiki-mcp looks at the wiki's own templates — the structured boxes you see
   on a page, like "Infobox nation" — to learn exactly which fields exist and
   what they're called. This matters because AI tools that try to edit wikis
   tend to invent field names that don't actually exist on that wiki; wiki-mcp
   checks against the real template first and refuses to write a field it
   doesn't recognize.
2. It compares the page's current content against the new data and produces a
   plain before/after: "this field changes from X to Y."
3. Depending on how a wiki is configured, wiki-mcp either stops there for a
   person to approve the change, or publishes it automatically.

wiki-mcp can also be used directly by an AI assistant (such as Claude) as a
set of tools for reading and editing a wiki, using a standard called MCP
(Model Context Protocol) — this is what lets an AI assistant ask "what would
this edit look like?" before anything is actually written to the wiki.

## Setting it up

You'll need Python installed. Then, from this project's folder:

```bash
pip install -e ".[mcp,dev]"
wiki-mcp init https://your-wiki-url-here
```

`init` will:
- Ask you to log in with a **bot password** — a separate, limited-permission
  login you create for this purpose on the wiki (under `Special:BotPasswords`
  on most wikis), not your normal account password.
- Look at the wiki's templates to learn their structure.
- Try to find the wiki's editing guidelines.
- Save everything it learned into a config file for that wiki, so this only
  needs to be done once per wiki.

Run `wiki-mcp harvest <wiki-name>` any time later to refresh a wiki's template
and guideline info (for example, after templates change) without logging in
again.

## Using it with an AI assistant

```bash
wiki-mcp serve
```

This starts a server that exposes the configured wiki(s) as tools an AI
assistant can call: read a page, check a template's fields, preview an edit,
publish an edit. After `init` or `harvest` finishes, it prints the exact
command needed to connect this to Claude Code — no manual setup required.

## Safety

- Nothing gets published without your say-so, unless a wiki is explicitly set
  to auto-publish.
- An edit is only ever allowed to change fields that genuinely exist on the
  wiki's template — an unrecognized field name is rejected, not written.
- Real credentials never appear in the project's config files or get shared
  anywhere; they're kept in a local, git-ignored file.

## For contributors

See [PLAN.md](PLAN.md) for the technical design and current limitations.
Licensed under [MIT](LICENSE).

## Running the tests

```bash
pytest
```
