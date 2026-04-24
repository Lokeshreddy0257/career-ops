"""Interview prep generator.

Given an evaluation, produce 5 STAR-structured behavioral stories the
user can rehearse before the phone screen / onsite. Each story is
grounded in specific cv.md chunks (same FAISS index we use for scoring)
so they don't drift into fabrication, which is the failure mode of
plain "generate interview stories for this JD" prompts.

Selection strategy:
  1. Pull the evaluation's top-scoring dimensions (proxy for "what the
     interviewer is most likely to probe on").
  2. For each of those dimensions, retrieve top-k CV chunks via the same
     FAISS index as the evaluator.
  3. Ask the LLM to synthesize one STAR story per dimension, citing the
     chunk_ids it drew from.
  4. Post-check that every cited chunk_id exists in the CV — same
     contract as tailor.py.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .embeddings import load_index, retrieve_for_requirements
from .llm import complete_json
from . import storage


class Story(BaseModel):
    dimension_id: str
    likely_question: str
    situation: str
    task: str
    action: str
    result: str
    source_chunk_ids: list[str] = Field(default_factory=list)
    followup_questions: list[str] = Field(default_factory=list)


class PrepPack(BaseModel):
    evaluation_id: int
    company: str
    title: str
    stories: list[Story]


class _LLMPrepResponse(BaseModel):
    stories: list[Story]


_PREP_PROMPT = """You are Lokesh Reddy's interview coach. Produce 5
STAR-structured behavioral stories for the upcoming interview at
{company} for the role "{title}". Each story must be grounded in
specific CV chunks (cite their `chunk_id`). Do NOT invent experience
that isn't in the CV.

## JD summary

{jd}

## Top rubric dimensions (likely areas the interviewer probes)

{top_dims_block}

## CV chunks available to draw from

{chunks_block}

## Output schema (strict JSON)

{{
  "stories": [
    {{
      "dimension_id": "technical_fit_genai",
      "likely_question": "Tell me about a time you designed a RAG system from scratch.",
      "situation": "... 2-3 sentences ...",
      "task":      "... 1-2 sentences ...",
      "action":    "... 3-5 sentences, concrete technical choices ...",
      "result":    "... 1-2 sentences with measurable outcome ...",
      "source_chunk_ids": ["experience.intuit.b0", "skills.generative_ai_nlp"],
      "followup_questions": [
        "How did you evaluate retrieval quality?",
        "What broke in production and how did you fix it?"
      ]
    }}
  ]
}}

Rules:
- Produce exactly 5 stories, one per top dimension (or closest available
  if fewer dims have CV support).
- `source_chunk_ids` must reference chunk_ids shown above; do not
  invent.
- Keep STAR sections tight — this is a rehearsal script, not a novel.
- Include 2 followup questions per story, the kind a sharp interviewer
  would ask next.
- Weave specific numbers / tools / model names when they appear in the
  cited CV chunks. Never invent metrics.
"""


def prep_for_evaluation(evaluation_id: int) -> PrepPack:
    storage.init_db()
    for s in storage.session():
        row = s.get(storage.EvaluationRow, evaluation_id)
        if not row:
            raise LookupError(f"No evaluation {evaluation_id}")
        job = row.job
        company = job.company

        # 1. Top dimensions as "probe surface"
        top_dims = sorted(row.scores_json, key=lambda d: d["score"], reverse=True)[:5]
        top_dim_ids = [d["dimension_id"] for d in top_dims]

        # 2. Retrieve matching CV chunks per dim (reusing the evaluator's index)
        idx = load_index()
        chunks_by_id = {c.chunk_id: c for c in idx.chunks}

        queries = [d["reasoning"] or d["dimension_id"] for d in top_dims]
        hits_per_q = retrieve_for_requirements(queries, top_k=3)

        # union of all retrieved chunk_ids, keeping their text
        retrieved_ids: list[str] = []
        seen: set[str] = set()
        for hits in hits_per_q:
            for h in hits:
                if h.chunk.chunk_id not in seen:
                    retrieved_ids.append(h.chunk.chunk_id)
                    seen.add(h.chunk.chunk_id)

        # 3. Build prompt
        top_dims_block = "\n".join(
            f"- {d['dimension_id']} ({d['score']}/5): {d['reasoning']}"
            for d in top_dims
        )
        chunks_block = "\n".join(
            f"- [{cid}] ({chunks_by_id[cid].section}) {chunks_by_id[cid].text}"
            for cid in retrieved_ids if cid in chunks_by_id
        )

        prompt = _PREP_PROMPT.format(
            company=company.name,
            title=job.title,
            jd=job.jd_text[:5000],
            top_dims_block=top_dims_block,
            chunks_block=chunks_block,
        )
        resp = complete_json(prompt, schema=_LLMPrepResponse)

        # 4. Post-check: cited chunk_ids must exist
        bad: list[tuple[int, str]] = []
        for i, story in enumerate(resp.stories):
            for cid in story.source_chunk_ids:
                if cid not in chunks_by_id:
                    bad.append((i, cid))
        if bad:
            raise ValueError(
                "UNTRACEABLE_CITATION — interview-prep response referenced "
                f"chunk_ids not in cv.md: {bad}"
            )

        return PrepPack(
            evaluation_id=evaluation_id,
            company=company.name,
            title=job.title,
            stories=resp.stories,
        )

    raise RuntimeError("no session")


def render_markdown(pack: PrepPack) -> str:
    """Convert a PrepPack to a reviewable markdown doc (useful for the
    CLI and for saving under artifacts/ for rehearsal)."""
    lines = [
        f"# Interview prep: {pack.title} @ {pack.company}",
        f"*Evaluation id: {pack.evaluation_id}*",
        "",
    ]
    for i, s in enumerate(pack.stories, 1):
        lines += [
            f"## Story {i} — {s.dimension_id}",
            "",
            f"**Likely question:** {s.likely_question}",
            "",
            "**Situation**  ",
            s.situation,
            "",
            "**Task**  ",
            s.task,
            "",
            "**Action**  ",
            s.action,
            "",
            "**Result**  ",
            s.result,
            "",
            "**Followups to expect**",
            *[f"- {q}" for q in s.followup_questions],
            "",
            f"*Sources:* `{', '.join(s.source_chunk_ids)}`",
            "",
        ]
    return "\n".join(lines)
