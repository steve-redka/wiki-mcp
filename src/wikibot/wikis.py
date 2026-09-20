"""Per-wiki configuration: loading, secrets resolution."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class PublishMode(str, Enum):
    DRY_RUN = "dry_run"
    REVIEW = "review"
    AUTO = "auto"


class WikiConfig(BaseModel):
    """Public, committable config for a single wiki. No secrets live here."""

    name: str
    site: str
    api_path: str = "/api.php"
    bot_username: str  # MainAccountUsername@BotPasswordName — not secret, just an identifier
    secrets_ref: str
    publish_mode: PublishMode = PublishMode.REVIEW
    template_schema_dir: str = "templates/"
    guideline_pages: list[str] = Field(default_factory=list)
    guidelines_dir: str = "guidelines/"

    @property
    def api_url(self) -> str:
        return self.site.rstrip("/") + self.api_path

    def resolve_secret(self) -> str:
        """Resolve secrets_ref, e.g. 'env:OWB_BOT_PASSWORD', to the actual value."""
        if self.secrets_ref.startswith("env:"):
            var_name = self.secrets_ref.removeprefix("env:")
            value = os.environ.get(var_name)
            if value is None:
                raise RuntimeError(f"Environment variable {var_name!r} is not set")
            return value
        raise ValueError(f"Unsupported secrets_ref scheme: {self.secrets_ref!r}")


def load_wiki_config(path: str | Path) -> WikiConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    data.setdefault("name", path.stem)
    return WikiConfig.model_validate(data)


def config_dir_for(config_path: str | Path) -> Path:
    """Directory alongside a wiki config where its template cache/guidelines live."""
    path = Path(config_path)
    return path.parent / path.stem
