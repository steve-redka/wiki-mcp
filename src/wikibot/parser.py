"""Wikitext <-> template parameters, via mwparserfromhell.

Editing through the parse tree (instead of regex/string replace) means only
the targeted template parameters change — surrounding prose, images, and
categories are left untouched.
"""

from __future__ import annotations

import mwparserfromhell
from mwparserfromhell.nodes import Template


def _normalize(name: str) -> str:
    return name.strip().replace("_", " ").lower()


def find_templates(wikitext: str, template_name: str) -> list[Template]:
    """All invocations of a template (case/underscore-insensitive) on a page."""
    code = mwparserfromhell.parse(wikitext)
    return _find_in_code(code, template_name)


def _find_in_code(code: mwparserfromhell.wikicode.Wikicode, template_name: str) -> list[Template]:
    normalized = _normalize(template_name)
    return [t for t in code.filter_templates() if _normalize(str(t.name)) == normalized]


def get_params(template: Template) -> dict[str, str]:
    return {str(p.name).strip(): str(p.value).strip() for p in template.params}


def set_params(wikitext: str, template_name: str, params: dict[str, str]) -> str:
    """Return wikitext with the given params set on the first invocation of
    template_name, adding params that don't yet exist and leaving all other
    params/content untouched.
    """
    code = mwparserfromhell.parse(wikitext)
    templates = _find_in_code(code, template_name)
    if not templates:
        raise ValueError(f"Template {template_name!r} not found on page")
    template = templates[0]
    for name, value in params.items():
        template.add(name, value)
    return str(code)
