"""Structural tests for prep.py. The LLM call is not exercised here;
these tests pin the rendering + contract surfaces.
"""

from __future__ import annotations

from career_ops.prep import PrepPack, Story, render_markdown


def _sample_pack() -> PrepPack:
    return PrepPack(
        evaluation_id=1,
        company="Anthropic",
        title="MTS, Applied AI",
        stories=[
            Story(
                dimension_id="technical_fit_genai",
                likely_question="Walk me through a RAG system you built.",
                situation="At Intuit, support agents had no unified tax/product answer surface.",
                task="Design an end-to-end RAG copilot across tax guidance and docs.",
                action="Chunked sources by section, embedded with Sentence Transformers, "
                       "indexed in FAISS, tuned retrieval thresholds with human-in-loop.",
                result="Agent answer accuracy improved; citation-grounded responses "
                       "reduced escalations.",
                source_chunk_ids=["experience.intuit.b0", "experience.intuit.b2"],
                followup_questions=[
                    "How did you evaluate retrieval quality offline?",
                    "What broke first at production scale?",
                ],
            ),
        ],
    )


def test_pack_shape() -> None:
    pack = _sample_pack()
    assert pack.company == "Anthropic"
    assert len(pack.stories) == 1
    s = pack.stories[0]
    assert s.dimension_id == "technical_fit_genai"
    assert len(s.source_chunk_ids) == 2
    assert len(s.followup_questions) == 2


def test_render_markdown_has_all_sections() -> None:
    md = render_markdown(_sample_pack())
    assert "# Interview prep" in md
    assert "## Story 1 — technical_fit_genai" in md
    for label in ("**Likely question:**", "**Situation**", "**Task**",
                  "**Action**", "**Result**", "**Followups to expect**"):
        assert label in md, f"missing {label}"
    # citations appear
    assert "experience.intuit.b0" in md


def test_render_empty_pack() -> None:
    empty = PrepPack(evaluation_id=99, company="X", title="Y", stories=[])
    md = render_markdown(empty)
    assert "# Interview prep: Y @ X" in md
