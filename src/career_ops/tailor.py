"""Tailored-CV generator.

Given an Evaluation, rewrites cv.md to foreground bullets that were cited
highly, emits a Markdown CV, renders it to HTML via Jinja, and prints a
PDF via WeasyPrint.

**Hard rule:** every bullet in the tailored CV must trace back to a
bullet in cv.md. We enforce this by requiring the LLM to emit, for each
output bullet, the `source_chunk_id` it derives from. A post-check
validates those ids exist in the current FAISS index.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import load_profile, settings
from .embeddings import load_index
from .llm import complete_json
from .schemas import TailoredBullet, TailoredCV
from . import storage


class _LLMTailorResponse(BaseModel):
    summary: str
    bullets: list[TailoredBullet]
    emphasis_tags: list[str]


_TAILOR_PROMPT = """You are rewriting Lokesh Reddy's resume for a specific
job. You MAY reorder, reword, and drop bullets. You MAY NOT invent new
experience. Every output bullet must trace back to a `source_chunk_id`
from the CV excerpts below.

## Job

Company: {company}
Title: {title}
JD:
{jd}

## Top-scoring rubric dimensions for this job

{top_dimensions}

## CV chunks (full inventory)

{chunks_block}

## Output schema (JSON only)

{{
  "summary": "3-5 sentence summary foregrounding relevant work",
  "bullets": [
    {{
      "source_chunk_id": "experience.intuit.b2",
      "rewritten": "Designed a RAG copilot using FAISS + Sentence Transformers...",
      "section": "experience"
    }},
    ...
  ],
  "emphasis_tags": ["rag", "faiss", "langchain", ...]
}}
"""


def tailor_for_evaluation(evaluation_id: int) -> TailoredCV:
    storage.init_db()

    for s in storage.session():
        row = s.get(storage.EvaluationRow, evaluation_id)
        if not row:
            raise LookupError(f"No evaluation {evaluation_id}")
        job = row.job
        company = job.company

        idx = load_index()
        chunks_by_id = {c.chunk_id: c for c in idx.chunks}

        top_dims_block = _format_top_dimensions(row.scores_json)
        chunks_block = "\n".join(
            f"- [{c.chunk_id}] ({c.section}) {c.text}"
            for c in idx.chunks
        )

        prompt = _TAILOR_PROMPT.format(
            company=company.name,
            title=job.title,
            jd=job.jd_text[:8000],
            top_dimensions=top_dims_block,
            chunks_block=chunks_block,
        )
        resp = complete_json(prompt, schema=_LLMTailorResponse)

        # Post-check: every source_chunk_id exists.
        bad = [b for b in resp.bullets if b.source_chunk_id not in chunks_by_id]
        if bad:
            raise ValueError(
                "UNTRACEABLE_BULLET — tailor response referenced chunk_ids "
                f"not in cv.md: {[b.source_chunk_id for b in bad]}"
            )

        md = _render_markdown(resp.summary, resp.bullets, profile=load_profile())
        pdf_path = _render_pdf(md, company=company.name)

        return TailoredCV(
            evaluation_id=evaluation_id,
            bullets=resp.bullets,
            emphasis_tags=resp.emphasis_tags,
            markdown=md,
            pdf_path=str(pdf_path),
        )

    raise RuntimeError("no session")


# ── Formatting ───────────────────────────────────────────────────────

def _format_top_dimensions(scores_json: list[dict[str, Any]]) -> str:
    # pick top-3 dimensions by score
    top = sorted(scores_json, key=lambda d: d["score"], reverse=True)[:3]
    return "\n".join(
        f"- {d['dimension_id']} ({d['score']}/5): {d['reasoning']}"
        for d in top
    )


def _render_markdown(summary: str, bullets: list[TailoredBullet], profile: dict) -> str:
    identity = profile.get("identity", {})
    lines = [
        f"# {identity.get('name', 'Lokesh Reddy')}",
        "",
        f"{identity.get('email', '')} · {identity.get('phone', '')} · "
        f"{identity.get('location', '')} · [LinkedIn]({identity.get('linkedin', '')})",
        "",
        "## Summary",
        "",
        summary,
        "",
    ]
    # group bullets by section, keep stable order
    section_order = ["experience", "skills", "education", "certifications"]
    by_section: dict[str, list[TailoredBullet]] = {}
    for b in bullets:
        by_section.setdefault(b.section, []).append(b)
    for section in section_order:
        if section not in by_section:
            continue
        lines.append(f"## {section.title()}")
        lines.append("")
        for b in by_section[section]:
            lines.append(f"- {b.rewritten}")
        lines.append("")
    return "\n".join(lines)


def _render_pdf(markdown_text: str, *, company: str) -> Path:
    """Render markdown → PDF via reportlab (pure Python, no system deps)."""
    import re
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    s = settings()
    s.artifacts_dir.mkdir(parents=True, exist_ok=True)
    slug = company.lower().replace(" ", "_").replace("/", "_")
    out = s.artifacts_dir / f"cv_{slug}_{date.today().isoformat()}.pdf"

    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
    )

    base = getSampleStyleSheet()
    styles = {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=18, spaceAfter=2, textColor=colors.HexColor("#1a1a2e"), alignment=TA_CENTER),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=3, textColor=colors.HexColor("#16213e"), borderPad=2),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontSize=10, spaceBefore=6, spaceAfter=2, textColor=colors.HexColor("#0f3460")),
        "body": ParagraphStyle("body", parent=base["Normal"], fontSize=9.5, leading=14, spaceAfter=3),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontSize=9.5, leading=14, leftIndent=12, spaceAfter=2, bulletIndent=0),
    }

    story = []
    for line in markdown_text.splitlines():
        line = line.rstrip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        # escape XML special chars
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # inline bold: **text**
        safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
        # inline italic: *text*
        safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

        if safe.startswith("# "):
            story.append(Paragraph(safe[2:], styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#16213e"), spaceAfter=4))
        elif safe.startswith("## "):
            story.append(Spacer(1, 6))
            story.append(Paragraph(safe[3:], styles["h2"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=2))
        elif safe.startswith("### "):
            story.append(Paragraph(safe[4:], styles["h3"]))
        elif safe.startswith("- ") or safe.startswith("* "):
            story.append(Paragraph(f"• {safe[2:]}", styles["bullet"]))
        else:
            story.append(Paragraph(safe, styles["body"]))

    doc.build(story)
    return out
