# wiki-mcp: Plan

## Purpose

A Python tool/MCP server for automatically editing MediaWiki-based wikis (starting
with wiki.gg, e.g. https://oldworldblues.wiki.gg), used to keep game-data wiki pages
(infoboxes, stat tables, focus trees, mechanics) in sync with data collected by a
separate, local scraping agent.

## Why both a library and an MCP server

- Simple, fully-structured field updates (e.g. "these three stats changed") don't
  need an LLM in the loop, since a deterministic batch pass is cheaper and safer.
- Structural changes (a nation has a unique focus tree or mechanics the generic
  template doesn't model) need judgment: an agent reasons over a diff before
  anything is written. MCP tools give an agent that capability without baking
  agent-specific logic into the core library.

## Repo structure (single repo, two internal packages)

```
wiki-mcp/
  src/
    wikibot/            # core library, no MCP/agent awareness
      client.py         # MediaWiki API session: BotPassword auth, tokens, rate limiting
      parser.py         # wikitext <-> template params via mwparserfromhell
      diff.py           # field-level diff between current page state and new data
      schema.py         # per-template param schemas (from TemplateData/doc/inference)
      validate.py        # checks proposed edits against cached template schemas
      wikis.py           # per-wiki config loading
    wiki_mcp/           # MCP server, depends on wikibot
      server.py
      tools.py          # get_page, propose/submit_edit, propose/submit_raw_edit, upload_file, search
    wiki_mcp_cli/        # `wiki-mcp init`, batch/dry-run CLI
  config/
    wikis/
      oldworldblues.yaml # example config, no secrets
  tests/
  PLAN.md
  README.md
  LICENSE
```

## Core library (`wikibot`)

- **Auth**: MediaWiki `Special:BotPasswords`. Works on any standard MediaWiki
  install (wiki.gg, Fandom, self-hosted, etc.); nothing wiki.gg-specific in code.
- **Fetch**: `action=query&prop=revisions` for current wikitext.
- **Parse**: `mwparserfromhell` to get templates as an AST, so specific params can
  be rewritten without disturbing unrelated prose, images, or categories.
- **Edit**: `action=edit` with edit token + base revision (conflict-safe, not
  last-write-wins).
- **Dry-run by default**: every edit path produces a diff object before any write.

## Anti-hallucination: template schema harvesting

LLMs tend to invent template/param names that don't exist on a given wiki. To
prevent that, schemas are harvested and cached per wiki, then used as a
validation gate before any edit is proposed or submitted:

1. Try `action=templatedata` (TemplateData extension, check per-wiki).
2. Fallback: parse `Template:X/doc` subpages for documented params.
3. Fallback: sample N live pages transcluding the template and infer
   params/typical value shapes empirically via `mwparserfromhell`.
4. Cache as JSON under `config/wikis/<wiki>/templates/`.
5. `validate.py` rejects/flags any param in a proposed edit that isn't in the
   cached schema, instead of silently writing it.

## Free-form edits: `propose_raw_edit` / `submit_raw_edit`

`propose_edit`/`submit_edit` can only change a template's own fields; they
can't add a new section, rewrite prose, or add content (like a focus-tree
block) that isn't just filling in an existing template's params. For that,
`propose_raw_edit`/`submit_raw_edit` take a full replacement wikitext for the
page and produce a unified diff, gated by the same `publish_mode` rules as
`submit_edit`.

The tradeoff: there's no schema to validate against for free text, so this
path has no anti-hallucination check at all. The `publish_mode: review` gate
(human approves the diff before it's written) is the main safety net here,
not param validation.

## Config generation: `wiki-mcp init <wiki-url>`

Bootstraps a new wiki's config instead of hand-writing it:

1. **Site discovery**: resolve `api.php`, confirm it's a live MediaWiki API, pull
   `siteinfo` (version, installed extensions; this is how TemplateData
   availability gets checked, not assumed).
2. **Credentials**: prompts for a Bot Password, test-logs-in immediately so a
   typo fails fast, writes to a local gitignored secrets file (`.env` /
   `secrets.yaml`). Never committed, never in the main config.
3. **Template scope prompt**: one cheap call (`list=allpages&apnamespace=10`,
   titles only) counts templates in the Template namespace, then prompts the
   user with that count rather than deciding silently:
   - Small count (e.g. dozens): offers full harvest as the default
     ("Found 34 templates. Harvest schemas for all of them? [Y/n]").
   - Large count (e.g. thousands, Wikipedia-scale): offers scoped mode as the
     default ("Found 6,412 templates, likely mostly unrelated to infoboxes.
     Point me at a few sample pages instead? [Y/n]").
   Harvesting cost itself isn't the concern for typical gaming wikis:
   `action=templatedata` batches ~50 titles per call, so even a few hundred
   templates harvest in a handful of requests. At Wikipedia-scale the real
   problem is signal-to-noise (most templates are citation/formatting/navbox,
   irrelevant to infobox data), which is why scoped mode is offered there.
4. **Template schemas**: runs the harvesting pipeline above (TemplateData,
   then `/doc`, then empirical sampling) over whichever scope was chosen, caches results.
5. **Guidelines**: semi-automatic; tries common page names (`Project:Manual of
   Style`, `Project:Bot policy`, `Help:Editing`, etc.) via the API; also accepts a
   manual list of guideline page titles in config, since conventions differ
   wiki-to-wiki and full auto-discovery isn't reliable. Fetched text becomes
   reference material the agent loads before proposing edits.
6. **Write config + summary**: emits `config/wikis/<name>.yaml` (public,
   committable) + secrets file (gitignored) + `templates/*.json` cache, then
   prints what was found (template count, TemplateData vs. inferred, guideline
   pages found vs. missing) for review before anything ever gets written to the
   wiki.

## Config schema (per wiki, reusable across wikis)

```yaml
site: https://oldworldblues.wiki.gg
api_path: /api.php
bot_username: ProfaneServitor@Bot
secrets_ref: env:OWB_BOT_PASSWORD
publish_mode: review        # dry_run | review | auto
template_schema_dir: templates/
guideline_pages: [Project:Manual of Style]
```

`publish_mode` is configurable per wiki and overridable per run (a `--dry-run`
flag always wins over config).

## Data flow

Scraper (separate, local, out of scope for this repo) produces structured JSON
per entity, `wikibot` maps it against the target wiki's template schema,
simple stat/field mismatches are auto-filled via CLI batch run, and structural
changes (new sections, unique mechanics not covered by the generic template) go
through MCP tools so an agent decides how to restructure the page, produces a
diff, and, depending on `publish_mode`, either stops for human approval or
publishes directly.

## Open-source considerations

- MIT or Apache-2.0 license.
- No live secrets or wiki-specific hacks in the repo; wiki.gg ships only as an
  example config.
- README walks through `pip install wiki-mcp`, then `wiki-mcp init`, then run.
- `wikibot` targets the generic MediaWiki API so any MediaWiki site works, not
  just wiki.gg.

## Open questions / not yet decided

- Whether wiki.gg specifically runs the TemplateData extension (checked live via
  `siteinfo` during `init`, not assumed).

## Todo
- Fix credential leakage issue