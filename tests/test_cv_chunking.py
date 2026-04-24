"""Structural tests for the CV chunker (no embedding model loaded)."""

from __future__ import annotations

from pathlib import Path

from career_ops.embeddings import _chunk_cv


CV_PATH = Path(__file__).resolve().parents[1] / "cv.md"


def test_chunker_produces_chunks() -> None:
    text = CV_PATH.read_text(encoding="utf-8")
    chunks = _chunk_cv(text)
    assert len(chunks) > 10, "expected at least ~10 chunks from cv.md"


def test_experience_chunks_tagged_by_company() -> None:
    text = CV_PATH.read_text(encoding="utf-8")
    chunks = _chunk_cv(text)
    companies = {c.company for c in chunks if c.section == "experience"}
    # "bank", "intuit", "celebal" are what parse_company extracts
    assert any("intuit" in (c or "") for c in companies)
    assert any("bank" in (c or "") for c in companies)


def test_tech_tags_detected() -> None:
    text = CV_PATH.read_text(encoding="utf-8")
    chunks = _chunk_cv(text)
    all_tags = {t for c in chunks for t in c.tech_tags}
    # things we know are in Lokesh's CV
    for expected in ("rag", "faiss", "langchain", "python"):
        assert expected in all_tags, f"missing tag {expected}"
