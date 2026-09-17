"""Validation gate: catches hallucinated template/param names before an edit
is proposed or submitted, by checking against harvested TemplateSchemas.
"""

from __future__ import annotations

from pydantic import BaseModel

from wikibot.schema import TemplateSchema


class ValidationResult(BaseModel):
    template_name: str
    unknown_params: list[str]
    missing_required_params: list[str]

    @property
    def ok(self) -> bool:
        return not self.unknown_params and not self.missing_required_params


def validate_params(schema: TemplateSchema, proposed_params: dict[str, str]) -> ValidationResult:
    """Compare proposed param names against a template's known schema.

    Unknown params are not silently written — callers should surface them
    for review rather than passing them through to edit_page.
    """
    known = schema.param_names
    proposed = set(proposed_params)
    required = {p.name for p in schema.params if p.required}
    return ValidationResult(
        template_name=schema.template_name,
        unknown_params=sorted(proposed - known),
        missing_required_params=sorted(required - proposed),
    )
