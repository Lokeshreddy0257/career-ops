"""Load and validate the YAML config files (profile / rubric / targets / portals)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Env-var driven runtime settings."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="CAREER_OPS_")

    # LLM — set llm_provider or let it auto-detect from which key is present
    llm_provider: str = "auto"               # "auto" | "gemini" | "anthropic" | "openai"
    gemini_model: str = "gemini-2.0-flash"
    anthropic_model: str = "claude-sonnet-4-5"
    openai_model: str = "gpt-4o"
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # Storage
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'career.db'}"
    faiss_path: Path = REPO_ROOT / "data" / "cv.faiss"
    faiss_meta_path: Path = REPO_ROOT / "data" / "cv.faiss.meta.json"

    # Embeddings
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Paths
    cv_path: Path = REPO_ROOT / "cv.md"
    profile_path: Path = REPO_ROOT / "config" / "profile.yml"
    rubric_path: Path = REPO_ROOT / "config" / "rubric.yml"
    targets_path: Path = REPO_ROOT / "config" / "targets.yml"
    portals_path: Path = REPO_ROOT / "config" / "portals.yml"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    templates_dir: Path = REPO_ROOT / "templates"


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def load_profile() -> dict[str, Any]:
    return _load_yaml(settings().profile_path)


@lru_cache(maxsize=1)
def load_rubric() -> dict[str, Any]:
    return _load_yaml(settings().rubric_path)


@lru_cache(maxsize=1)
def load_targets() -> dict[str, Any]:
    return _load_yaml(settings().targets_path)


@lru_cache(maxsize=1)
def load_portals() -> dict[str, Any]:
    return _load_yaml(settings().portals_path)


class RubricDimension(BaseModel):
    id: str
    name: str
    weight: float
    description: str
    anchors: dict[int, str] = Field(default_factory=dict)


class Rubric(BaseModel):
    version: str
    scale: list[int]
    grade_thresholds: dict[str, int]
    dimensions: list[RubricDimension]

    @classmethod
    def current(cls) -> "Rubric":
        return cls.model_validate(load_rubric())

    def dimension(self, dim_id: str) -> RubricDimension:
        for d in self.dimensions:
            if d.id == dim_id:
                return d
        raise KeyError(f"Unknown rubric dimension: {dim_id}")

    @property
    def total_weight(self) -> float:
        return sum(d.weight for d in self.dimensions)
