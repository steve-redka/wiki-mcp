# wiki-mcp
## What

wiki-mcp is a tool fo automatic fandom wiki editing using LLMs. You can either tell it to do edits from chat, or pair it with scraper to collect data. Intended for automatic editing of gaming and literature wikis.

## Setting it up

You'll need Python installed. Then, from this project's folder:

```bash
pip install -e ".[mcp,dev]"
wiki-mcp init https://your-wiki-url-here
```

`init` will:
- Ask you to log in with a **bot password**, a separate, limited-permission
  login you create for this purpose on the wiki (under `Special:BotPasswords`
  on most wikis), not your normal account password.
- Look at the wiki's templates to learn their structure.
- Try to find the wiki's editing guidelines, and cache their text locally
  (along with a `guidelines/custom.md` file you can edit yourself, for house
  style the wiki's own guidelines wouldn't think to mention). A raw
  guideline page is usually as much account setup and Discord etiquette as
  actual editing rules; ask your agent to read `guidelines/harvested.json`
  and write a condensed, rules-only version to `guidelines/condensed.json`
  (same title keys), and it's used instead of the raw text.
- Optionally download the wiki's page/redirect titles, so proposed edits can
  flag `[[links]]` that don't actually resolve to a real page.
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
command needed to connect this to Claude Code, so no manual setup is required.

## Safety

- Nothing gets published without your say-so, unless a wiki is explicitly set
  to auto-publish.
- Template-based edits are only ever allowed to change fields that genuinely
  exist on the wiki's template; an unrecognized field name is rejected, not
  written. Full-page edits (for prose, new sections, anything outside a
  template's fields) skip that check since there's no fixed schema for free
  text, so review those diffs more carefully, especially outside auto mode.
- Both edit paths flag `[[links]]` that don't resolve against the wiki's
  page/redirect index (with a suggested real title), so a hallucinated link
  target shows up before it's written instead of after. Advisory only, since
  a link to a page that doesn't exist yet can be intentional.
- Real credentials never appear in the project's config files or get shared
  anywhere; they're kept in a local, git-ignored file.

## Editing large or whole pages

For full-page edits, prefer `propose_patch_edit`/`submit_patch_edit` over
`propose_raw_edit`/`submit_raw_edit` whenever you're changing existing text
rather than adding something new. The raw-edit tools take a full replacement
page/section, which on a large article means an AI assistant has to
regenerate the entire thing on every single edit (burning tokens) and keep a
copy of it in its context for the rest of the session. The patch tools take
`old_string`/`new_string` instead, a find-and-replace against the live page
applied server-side, so only the part actually changing has to pass through
the assistant at all. It still needs to have read the page first (a plain
`get_page` call) to know what to match, but that's a one-time cost rather
than one paid on every edit.

`old_string` has to match the current text exactly once, so include enough
surrounding context to make it unique. Reach for `propose_raw_edit`/
`submit_raw_edit` instead when there's genuinely nothing yet to match
against, such as a brand-new section or a brand-new page.

## Examples

todo

## For contributors

See [PLAN.md](PLAN.md) for the technical design and current limitations.
Licensed under [MIT](LICENSE).

## Running the tests

```bash
pytest
```
