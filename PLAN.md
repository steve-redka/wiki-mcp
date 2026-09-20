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
      guidelines.py      # harvests/caches wiki guideline pages + user's custom.md
      links.py           # harvests/caches page+redirect index, flags unresolved [[links]]
      wikis.py           # per-wiki config loading
    wiki_mcp/           # MCP server, depends on wikibot
      server.py
      tools.py          # get_page, list_sections, get_guidelines, propose/submit_edit, propose/submit_raw_edit, upload_file, search
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

## Anti-hallucination: link checking

The same problem template params have (an LLM inventing a name that doesn't
exist) shows up for `[[wikilinks]]` in prose: an agent asked to link a
concept will confidently write `[[Rio Grande Republic]]` when the real page
is `Republic of the Rio Grande`. There's no template schema to catch that,
since it's free text, so instead:

1. `wiki-mcp init`/`harvest` optionally batch-fetches every mainspace page
   title and redirect alias via `generator=allpages&redirects=1`, paginating
   through the whole namespace once rather than looking a link up live per
   call, and caches it as `pages.json` under the wiki's config directory.
2. `propose_edit`/`propose_raw_edit` parse `[[...]]` targets out of the
   proposed text and check each one against that cache (title/redirect
   match, underscore/first-letter-insensitive, same as MediaWiki itself).
   Anything that doesn't resolve comes back as `link_issues`, each with a
   fuzzy-matched suggestion (word-overlap first, so a reordered title like
   the RRG example above still finds its match, falling back to
   character-similarity for typos).
3. This is advisory, not a validation gate that blocks the edit: a link to a
   page that doesn't exist yet (a redlink) is normal, deliberate wiki
   practice, and nothing here can tell that apart from a hallucinated title.
   It's there for whoever reviews the diff (human, in `review` mode, or the
   agent itself) to catch the RRG-style mistake before it ships.

## Editing guidelines: caching and personal preferences

`guideline_pages` (wiki-discovered pages like `Project:Manual of Style`)
used to only store titles — an agent still had to fetch each one live with
`get_page`, once per session at best, spending a tool round-trip per page.
`wiki-mcp init`/`harvest` now also fetches their wikitext and caches it
locally (`guidelines/harvested.json`), and a `get_guidelines(wiki)` tool
returns the cached, combined text in one call with no network access.

Wiki style guides don't cover everything an editor might want to hold to —
a wiki's Manual of Style has no reason to mention that in-universe faction
tags like `(RRG)` shouldn't be glossed in prose, since human editors aren't
tempted to write that. `guidelines/custom.md`, alongside the harvested
cache, is a plain-text file the user edits directly with whatever house
style or pet peeves they want an agent to follow; `get_guidelines` appends
it after the harvested pages. It's local and gitignored like the rest of
`config/wikis/`, so it stays wiki-specific instead of a single global style
sheet.

## Free-form edits: `propose_raw_edit` / `submit_raw_edit`

`propose_edit`/`submit_edit` can only change a template's own fields; they
can't add a new section, rewrite prose, or add content (like a focus-tree
block) that isn't just filling in an existing template's params. For that,
`propose_raw_edit`/`submit_raw_edit` take a replacement wikitext and produce
a unified diff, gated by the same `publish_mode` rules as `submit_edit`.

The tradeoff: there's no schema to validate against for free text, so this
path has no anti-hallucination check at all. The `publish_mode: review` gate
(human approves the diff before it's written) is the main safety net here,
not param validation.

Both tools take an optional `section` (an index from `list_sections`, or
"new" to append a section), scoping the read/diff/write to just that section
instead of the whole page. This isn't just a convenience: an agent's tool
call has to fit in a single turn's output, so a full-page edit on a large
page (hundreds of KB) can hit that ceiling even when the actual change is a
handful of rows. Section scoping means the call only ever needs to carry the
changed section's text, not the unrelated rest of the page. `get_page` takes
the same `section` parameter for the read side of the same problem.

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
5. **Page/redirect index** (optional, prompted): batch-fetches mainspace
   page titles and redirects, caches as `pages.json` — see "Anti-hallucination:
   link checking" below.
6. **Guidelines**: semi-automatic; tries common page names (`Project:Manual of
   Style`, `Project:Bot policy`, `Help:Editing`, etc.) via the API, then falls
   back to a full-text search (`editing guide`, `style guide`, `new editor`,
   etc.) for wiki-specific names those guesses would miss (e.g. "New Editor's
   Guide to OWB Wiki Editing") and pre-fills the results into the "any other
   titles" prompt for the user to confirm; also accepts a manual list of
   guideline page titles in config, since conventions differ wiki-to-wiki and
   full auto-discovery isn't reliable. Their wikitext is fetched and cached
   locally (`guidelines/harvested.json`) alongside a `guidelines/custom.md`
   starter file for the user's own preferences — see "Editing guidelines"
   above.
7. **Write config + summary**: emits `config/wikis/<name>.yaml` (public,
   committable) + secrets file (gitignored) + `templates/*.json`/`pages.json`/
   `guidelines/` caches, then prints what was found (template count,
   TemplateData vs. inferred, guideline pages found vs. missing) for review
   before anything ever gets written to the wiki.

## Config schema (per wiki, reusable across wikis)

```yaml
site: https://oldworldblues.wiki.gg
api_path: /api.php
bot_username: ProfaneServitor@Bot
secrets_ref: env:OWB_BOT_PASSWORD
publish_mode: review        # dry_run | review | auto
template_schema_dir: templates/
guideline_pages: [Project:Manual of Style]
guidelines_dir: guidelines/
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