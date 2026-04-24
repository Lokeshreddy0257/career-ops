"""Take a JD URL or text and produce a JobOffer persisted to the DB.

MVP strategy:
- URL on a known portal (Greenhouse / Lever / Ashby) → use the public
  JSON API declared in `config/portals.yml`.
- Arbitrary URL → fetch HTML, extract main content with BeautifulSoup
  (portals.py has the Playwright fallback for heavy-JS sites).
- Path or raw text → treat as JD body.

In all cases, after we have the JD body we call the LLM once to extract
structured fields: company, title, location, seniority hint, comp hint,
and a bullet list of must-have / nice-to-have requirements.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify
from pydantic import BaseModel

from .config import load_portals, load_targets, settings
from .llm import complete_json
from .schemas import JobOffer, JobRequirement
from . import storage


# ── Structured extraction schema ────────────────────────────────────

class _ExtractedJD(BaseModel):
    company: str
    title: str
    location: str | None = None
    seniority: str | None = None
    comp_text: str | None = None
    must_have: list[str] = []
    nice_to_have: list[str] = []
    tech_tags: list[str] = []


_EXTRACT_PROMPT = """You are parsing a job description. Return strict JSON
matching this schema:

{{
  "company": "string",
  "title": "string",
  "location": "string | null",
  "seniority": "Junior|Mid|Senior|Staff|Principal|null",
  "comp_text": "string | null (raw comp text if present)",
  "must_have": ["bullet 1", "..."],
  "nice_to_have": ["bullet 1", "..."],
  "tech_tags": ["python","pytorch","rag", ...]
}}

Rules:
- Keep each requirement as a short, self-contained sentence.
- Do NOT invent requirements; only extract what is present.
- `tech_tags` must be lowercase slugs.

JD:
---
{jd}
---
"""


# ── Public API ───────────────────────────────────────────────────────

def ingest(source: str, *, source_kind: Literal["auto", "url", "text"] = "auto") -> int:
    """Ingest one JD and return the stored `jobs.id`."""
    jd_text, url, kind = _resolve_source(source, source_kind)
    extracted = _extract(jd_text)
    offer = JobOffer(
        company=extracted.company,
        title=extracted.title,
        location=extracted.location,
        seniority=extracted.seniority,
        comp_text=extracted.comp_text,
        jd_text=jd_text,
        requirements=[
            *(JobRequirement(text=r, kind="must") for r in extracted.must_have),
            *(JobRequirement(text=r, kind="nice") for r in extracted.nice_to_have),
        ],
        tech_tags=extracted.tech_tags,
        source_url=url,           # may be None
        source_kind=kind,
    )
    return _persist(offer)


# ── Source resolution ────────────────────────────────────────────────

def _resolve_source(source: str, source_kind: str) -> tuple[str, str | None, str]:
    """Return (jd_text, source_url, kind)."""
    if source_kind == "text" or (source_kind == "auto" and not _looks_like_path_or_url(source)):
        return source, None, "text"

    # path?
    path = Path(source)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8"), None, "text"

    # URL
    if _looks_like_url(source):
        body, kind = _fetch_url(source)
        return body, source, kind

    raise ValueError(f"Couldn't resolve ingestion source: {source[:120]!r}")


def _looks_like_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip()))


def _looks_like_path_or_url(s: str) -> bool:
    s = s.strip()
    return _looks_like_url(s) or s.startswith(("/", "./", "~")) or s.endswith((".txt", ".md"))


def _fetch_url(url: str) -> tuple[str, str]:
    """Try known portal JSON endpoints first; fall back to HTML-to-markdown."""
    portals = load_portals().get("portals", {})
    # heuristic detection
    if "boards.greenhouse.io" in url or "boards-api.greenhouse.io" in url:
        kind = "greenhouse"
    elif "jobs.lever.co" in url or "api.lever.co" in url:
        kind = "lever"
    elif "jobs.ashbyhq.com" in url or "api.ashbyhq.com" in url:
        kind = "ashby"
    else:
        kind = "url"

    with httpx.Client(timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": portals.get("rate_limits", {}).get("user_agent",
                                                                                "career-ops/0.1")}) as c:
        r = c.get(url)
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "json" in ctype:
            return r.text, kind
        html = r.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    body_md = markdownify(str(soup.body or soup), heading_style="ATX")
    return body_md, kind


# ── LLM extraction ───────────────────────────────────────────────────

def _extract(jd_text: str) -> _ExtractedJD:
    prompt = _EXTRACT_PROMPT.format(jd=jd_text[:12000])  # cap long JDs
    return complete_json(prompt, schema=_ExtractedJD)


# ── Persistence ──────────────────────────────────────────────────────

def _persist(offer: JobOffer) -> int:
    storage.init_db()
    targets = {t["name"]: t for t in load_targets().get("companies", [])}
    target_hit = targets.get(offer.company)

    for s in storage.session():
        company = storage.get_or_create_company(
            s,
            name=offer.company,
            h1b_history=(target_hit or {}).get("h1b_history", "unknown"),
            stage=(target_hit or {}).get("stage"),
            portal_url=(target_hit or {}).get("portal"),
            portal_type=(target_hit or {}).get("portal_type"),
        )
        job = storage.Job(
            company_id=company.id,
            title=offer.title,
            location=offer.location,
            url=str(offer.source_url) if offer.source_url else None,
            jd_text=offer.jd_text,
            parsed_requirements=[r.model_dump() for r in offer.requirements],
            source_kind=offer.source_kind,
        )
        s.add(job)
        s.commit()
        return job.id

    raise RuntimeError("session yielded nothing")
