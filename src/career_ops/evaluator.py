"""RAG-grounded rubric scorer.

Flow for one job:
    1. Load Job from DB, its parsed_requirements, and its company record.
    2. For each must/nice requirement, retrieve top-k CV chunks via FAISS.
    3. Compute the visa-sponsorship dimension *mechanically* from
       company.h1b_history (don't let the LLM make this up).
    4. Build a single prompt containing JD + retrieved chunks + rubric.
    5. Ask the LLM for a JSON DimensionScore[] covering the remaining
       dimensions.
    6. Splice visa score back in, rank, persist EvaluationRow.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .config import Rubric, load_profile
from .embeddings import retrieve_for_requirements
from .llm import complete_json
from .ranker import rank
from .schemas import DimensionScore, Evaluation
from . import storage


class _LLMScoreResponse(BaseModel):
    dimension_scores: list[DimensionScore]


_SCORE_PROMPT = """You score how well a job fits Lokesh Reddy's resume,
using a strict rubric and grounded citations. Respond with JSON only.

## Rubric (score EACH dimension 0-5 with reasoning and citations)

{rubric_block}

## Scoring conventions

- A citation is a `chunk_id` like "experience.intuit.b2". Only use
  chunk_ids that appear in the "CV excerpts" block below.
- If no CV content supports a dimension, use citations=[] and say so in
  the reasoning. Do not invent.
- Keep reasoning to 1-2 sentences.
- Skip the `visa_sponsorship` dimension in your output — we compute it
  separately.

## Profile summary

{profile_summary}

## CV excerpts (grouped by JD requirement)

{excerpts_block}

## Job description

Company: {company}
Title: {title}
Location: {location}
H-1B history (for your awareness only, don't score visa): {h1b}

{jd}

## Output schema

{{
  "dimension_scores": [
    {{
      "dimension_id": "<id from rubric>",
      "score": 0-5,
      "reasoning": "...",
      "citations": ["experience.intuit.b2", "skills.generative_ai_nlp"]
    }},
    ...
  ]
}}
"""


def evaluate_job(job_id: int) -> Evaluation:
    rubric = Rubric.current()
    profile = load_profile()
    storage.init_db()

    for s in storage.session():
        job = s.get(storage.Job, job_id)
        if not job:
            raise LookupError(f"No Job with id={job_id}")
        company = job.company

        # 1. retrieve
        requirements = [r["text"] for r in (job.parsed_requirements or [])]
        if not requirements:
            # fall back to a single retrieval over the whole JD
            requirements = [job.jd_text[:600]]
        hits_per_req = retrieve_for_requirements(requirements, top_k=3)

        # 2. visa dimension (mechanical)
        visa_score = _visa_score_from_history(company.h1b_history)

        # 3. LLM scoring for the remaining dimensions
        llm_dims = [d for d in rubric.dimensions if d.id != "visa_sponsorship"]
        rubric_block = "\n".join(
            f"- **{d.id}** ({d.name}, weight {d.weight}): {d.description}"
            for d in llm_dims
        )
        excerpts_block = _format_excerpts(requirements, hits_per_req)
        profile_summary = _format_profile_summary(profile)

        prompt = _SCORE_PROMPT.format(
            rubric_block=rubric_block,
            profile_summary=profile_summary,
            excerpts_block=excerpts_block,
            company=company.name,
            title=job.title,
            location=job.location or "unknown",
            h1b=company.h1b_history,
            jd=job.jd_text[:9000],
        )
        resp = complete_json(prompt, schema=_LLMScoreResponse)

        # 4. splice visa score back in
        scores = [
            s for s in resp.dimension_scores if s.dimension_id != "visa_sponsorship"
        ]
        scores.append(visa_score)

        # 5. rank
        weighted_total, percent, grade = rank(scores, rubric)

        evaluation = Evaluation(
            job_id=job.id,
            company=company.name,
            title=job.title,
            location=job.location,
            rubric_version=rubric.version,
            dimension_scores=scores,
            weighted_total=round(weighted_total, 4),
            percent=percent,
            grade=grade,
            model=_current_model_string(),
        )

        row = storage.EvaluationRow(
            job_id=job.id,
            rubric_version=rubric.version,
            model=evaluation.model,
            scores_json=[s.model_dump() for s in scores],
            weighted_total=weighted_total,
            percent=percent,
            grade=grade,
        )
        s.add(row)
        s.commit()

        return evaluation

    raise RuntimeError("no session")


# ── Visa scoring (mechanical) ────────────────────────────────────────

_H1B_MAP = {
    "heavy":       (5, "Active, consistent H-1B sponsor (10+ LCAs/year)."),
    "active":      (4, "Has sponsored H-1B in the last 3 years."),
    "occasional":  (3, "Has sponsored in the past but not recently."),
    "unknown":     (3, "Unknown sponsor history (small / private / stealth)."),
    "none":        (2, "No LCAs on file. Sponsorship unlikely without escalation."),
}


def _visa_score_from_history(h1b_history: str | None) -> DimensionScore:
    h1b = (h1b_history or "unknown").lower()
    score, reasoning = _H1B_MAP.get(h1b, _H1B_MAP["unknown"])
    return DimensionScore(
        dimension_id="visa_sponsorship",
        score=score,
        reasoning=f"H-1B history tag = '{h1b}'. {reasoning}",
        citations=[],
    )


# ── Formatting helpers ───────────────────────────────────────────────

def _format_excerpts(requirements: list[str], hits_per_req: list[list[Any]]) -> str:
    blocks = []
    for req, hits in zip(requirements, hits_per_req):
        lines = [f"- REQUIREMENT: {req}"]
        for h in hits:
            lines.append(f"  - [{h.chunk.chunk_id}] ({h.score:.2f}) {h.chunk.text}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "(no excerpts)"


def _format_profile_summary(profile: dict[str, Any]) -> str:
    sen = profile.get("seniority", {})
    comp = profile.get("compensation", {})
    auth = profile.get("work_authorization", {})
    return (
        f"- {sen.get('years_experience', '?')} YoE, target level: {sen.get('level_target')}\n"
        f"- Comp floor base ${comp.get('floor_base_usd')}, target TC ${comp.get('target_tc_usd')}\n"
        f"- Work auth: {auth.get('current_status')}, "
        f"needs sponsorship now={auth.get('requires_sponsorship_now')}, "
        f"future={auth.get('requires_sponsorship_future')}"
    )


def _current_model_string() -> str:
    from .config import settings
    s = settings()
    return f"{s.llm_provider}:{s.anthropic_model if s.llm_provider == 'anthropic' else s.openai_model}"
