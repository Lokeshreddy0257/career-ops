"""Pydantic schemas used across the engine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


# ── Ingestion ────────────────────────────────────────────────────────

class JobRequirement(BaseModel):
    text: str
    kind: Literal["must", "nice"] = "must"


class JobOffer(BaseModel):
    """Normalized job posting after ingestion."""

    company: str
    title: str
    location: str | None = None
    seniority: str | None = None
    comp_text: str | None = None
    jd_text: str
    requirements: list[JobRequirement] = Field(default_factory=list)
    tech_tags: list[str] = Field(default_factory=list)
    source_url: HttpUrl | None = None
    source_kind: Literal["url", "text", "greenhouse", "lever", "ashby", "workday"] = "text"
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


# ── Retrieval ────────────────────────────────────────────────────────

class CVChunk(BaseModel):
    chunk_id: str                   # e.g. "experience.intuit.3"
    section: str                    # "summary" | "experience" | "skills" | "education" | "cert"
    company: str | None = None      # for experience chunks
    text: str
    tech_tags: list[str] = Field(default_factory=list)


class RetrievalHit(BaseModel):
    chunk: CVChunk
    score: float                    # cosine / dot product
    matched_requirement: str


# ── Evaluation ───────────────────────────────────────────────────────

class DimensionScore(BaseModel):
    dimension_id: str
    score: int = Field(ge=0, le=5)
    reasoning: str
    citations: list[str] = Field(
        default_factory=list,
        description="chunk_ids from cv.md referenced in reasoning",
    )


class Evaluation(BaseModel):
    job_id: int
    company: str
    title: str
    location: str | None
    rubric_version: str
    dimension_scores: list[DimensionScore]
    weighted_total: float
    percent: float
    grade: str                       # "A+" … "F"
    model: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def by_dim(self, dim_id: str) -> DimensionScore | None:
        for d in self.dimension_scores:
            if d.dimension_id == dim_id:
                return d
        return None


# ── Tailoring ────────────────────────────────────────────────────────

class TailoredBullet(BaseModel):
    source_chunk_id: str            # must trace back to cv.md
    rewritten: str
    section: str


class TailoredCV(BaseModel):
    evaluation_id: int
    bullets: list[TailoredBullet]
    emphasis_tags: list[str]        # tech tags we foregrounded for this JD
    markdown: str                   # final markdown
    pdf_path: str | None = None
