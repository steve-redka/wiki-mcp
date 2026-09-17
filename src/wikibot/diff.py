"""Field-level diff between a template's current params and proposed new data."""

from __future__ import annotations

from pydantic import BaseModel


class ParamChange(BaseModel):
    name: str
    old_value: str | None
    new_value: str


class TemplateDiff(BaseModel):
    template_name: str
    changes: list[ParamChange]

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def render(self) -> str:
        lines = [f"--- {self.template_name} ---"]
        for change in self.changes:
            if change.old_value is None:
                lines.append(f"+ {change.name} = {change.new_value!r} (new)")
            else:
                lines.append(f"~ {change.name}: {change.old_value!r} -> {change.new_value!r}")
        return "\n".join(lines)


def diff_params(template_name: str, current: dict[str, str], proposed: dict[str, str]) -> TemplateDiff:
    """Only includes params that actually change — unchanged params (and
    params present on the page but not in `proposed`) are left out, since
    those aren't part of this edit.
    """
    changes = [
        ParamChange(name=name, old_value=current.get(name), new_value=new_value)
        for name, new_value in proposed.items()
        if current.get(name) != new_value
    ]
    return TemplateDiff(template_name=template_name, changes=changes)
