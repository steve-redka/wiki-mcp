"""Per-template parameter schemas: harvesting, caching, and the source-of-truth
used by validate.py to catch hallucinated template/param names before an edit
is written.

Harvest order (first one that yields data wins, per template):
  1. TemplateData (action=templatedata) — machine-readable, most reliable.
  2. Template:X/doc subpage — human-written docs, parsed heuristically.
  3. Empirical sampling — infer params from pages that transclude the template.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import mwparserfromhell
from pydantic import BaseModel

from wikibot.client import WikiClient
from wikibot.parser import find_templates, get_params

SchemaSource = Literal["templatedata", "doc", "inferred"]


class TemplateParam(BaseModel):
    name: str
    required: bool = False
    description: str | None = None


class TemplateSchema(BaseModel):
    template_name: str
    source: SchemaSource
    params: list[TemplateParam]

    @property
    def param_names(self) -> set[str]:
        return {p.name for p in self.params}


def harvest_templatedata(client: WikiClient, template_titles: list[str]) -> dict[str, TemplateSchema]:
    """Batch-fetch TemplateData for up to ~50 templates per call.

    template_titles may be bare ("Nation") or namespaced ("Template:Nation") —
    normalized to namespaced for the API call, since action=templatedata
    needs the full page title. Returned dict is keyed by the *bare* name,
    matching how templates are actually invoked on pages (see
    wikibot.parser.find_templates), not the namespaced page title.

    Templates with no TemplateData registered are simply absent from the
    result — callers should fall back to harvest_from_doc / harvest_by_sampling
    for those.
    """
    schemas: dict[str, TemplateSchema] = {}
    full_titles = [t if t.startswith("Template:") else f"Template:{t}" for t in template_titles]
    batch_size = 50
    for i in range(0, len(full_titles), batch_size):
        batch = full_titles[i : i + batch_size]
        payload = client.call({"action": "templatedata", "titles": "|".join(batch)})
        pages = payload.get("pages", {})
        for page in pages.values():
            title = page.get("title")
            params = page.get("params")
            if title is None or not params:
                continue
            bare_name = title.removeprefix("Template:")
            schemas[bare_name] = TemplateSchema(
                template_name=bare_name,
                source="templatedata",
                params=[
                    TemplateParam(
                        name=name,
                        required=bool(info.get("required", False)),
                        description=(info.get("description") or {}).get("en"),
                    )
                    for name, info in params.items()
                ],
            )
    return schemas


def harvest_from_doc(client: WikiClient, template_title: str) -> TemplateSchema | None:
    """Parse a Template:X/doc subpage for documented parameters.

    Looks for the common copy-paste convention: a <pre> block containing an
    example invocation of the template with blank param values, e.g.

        <pre>
        {{Nation
        |country_name=
        |stability=
        }}
        </pre>

    Returns None if there's no /doc page, or no such block is found — that's
    a normal "this fallback didn't resolve it either", not an error.
    """
    bare_name = template_title.removeprefix("Template:").removesuffix("/doc")
    doc_title = f"Template:{bare_name}/doc"
    page = client.get_page_if_exists(doc_title)
    if page is None:
        return None

    doc_code = mwparserfromhell.parse(page.wikitext)
    for pre in doc_code.filter_tags(matches=lambda t: str(t.tag) == "pre"):
        templates = find_templates(str(pre.contents), bare_name)
        if not templates:
            continue
        params = [TemplateParam(name=name) for name in get_params(templates[0])]
        if params:
            return TemplateSchema(template_name=bare_name, source="doc", params=params)
    return None


def harvest_by_sampling(client: WikiClient, template_title: str, sample_titles: list[str]) -> TemplateSchema | None:
    """Infer a template's params empirically from pages that use it — the
    param names actually in use across sample_titles become the schema. This
    mirrors how a human editor would copy an existing filled-in usage rather
    than trust (possibly missing or stale) documentation.

    Returns None only if none of sample_titles contain an invocation of the
    template at all — a template that's invoked bare (no params) on every
    sample is a legitimate empty-params result, not a failure.
    """
    bare_name = template_title.removeprefix("Template:")
    param_names: set[str] = set()
    found_any_invocation = False
    for title in sample_titles:
        page = client.get_page_if_exists(title)
        if page is None:
            continue
        for template in find_templates(page.wikitext, bare_name):
            found_any_invocation = True
            param_names.update(get_params(template))
    if not found_any_invocation:
        return None
    return TemplateSchema(
        template_name=bare_name,
        source="inferred",
        params=[TemplateParam(name=name) for name in sorted(param_names)],
    )


def find_pages_using_template(client: WikiClient, template_title: str, limit: int = 10) -> list[str]:
    """Pages that transclude a template, for sourcing samples when the caller
    doesn't already have candidate pages in hand (e.g. full-wiki harvesting,
    as opposed to the scoped mode where the user already named sample pages).
    """
    bare_name = template_title.removeprefix("Template:")
    payload = client.call(
        {
            "action": "query",
            "list": "embeddedin",
            "eititle": f"Template:{bare_name}",
            "eilimit": limit,
            "einamespace": 0,
        }
    )
    return [p["title"] for p in payload["query"]["embeddedin"]]


def save_schema(schema: TemplateSchema, directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{schema.template_name.replace('/', '_')}.json"
    path.write_text(schema.model_dump_json(indent=2))
    return path


def load_schema(path: str | Path) -> TemplateSchema:
    return TemplateSchema.model_validate_json(Path(path).read_text())


def load_all_schemas(directory: str | Path) -> dict[str, TemplateSchema]:
    directory = Path(directory)
    if not directory.exists():
        return {}
    return {s.template_name: s for s in (load_schema(p) for p in directory.glob("*.json"))}
