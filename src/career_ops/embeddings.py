"""FAISS + sentence-transformers index over cv.md.

The CV is split into atomic chunks (one per bullet, plus one per section
header) so that when a JD requirement is retrieved we get back the
*exact* bullet that backs it — which then becomes a citation in the
evaluation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .config import settings
from .schemas import CVChunk, RetrievalHit


# ── Chunking ─────────────────────────────────────────────────────────

_TECH_TAG_PATTERNS = {
    "python": r"\bpython\b",
    "fastapi": r"\bfastapi\b",
    "langchain": r"\blangchain\b",
    "rag": r"\brag\b|retrieval[- ]augmented",
    "faiss": r"\bfaiss\b",
    "sentence_transformers": r"sentence[- ]transformers?",
    "llm": r"\bllm\b|gpt-?4|claude|llama",
    "sqlalchemy": r"\bsqlalchemy\b",
    "docker": r"\bdocker\b",
    "kubernetes": r"\bkubernetes\b|\bk8s\b",
    "airflow": r"\bairflow\b",
    "mlflow": r"\bmlflow\b",
    "pytorch": r"\bpytorch\b",
    "tensorflow": r"\btensorflow\b",
    "aws": r"\baws\b",
    "azure": r"\bazure\b",
    "gcp": r"\bgcp\b|google cloud",
    "sql": r"\bsql\b",
    "nlp": r"\bnlp\b|transformer|bert|roberta",
    "vector_db": r"\bpinecone\b|\bchromadb\b|\bfaiss\b|vector database",
}


def _extract_tags(text: str) -> list[str]:
    lowered = text.lower()
    return [tag for tag, pat in _TECH_TAG_PATTERNS.items() if re.search(pat, lowered)]


def _chunk_cv(cv_markdown: str) -> list[CVChunk]:
    """Split cv.md into atomic chunks.

    Rules:
    - `## Summary` paragraph → one chunk.
    - Each `### <Role>` under Experience → one chunk for the role header
      + one chunk per bullet below it.
    - Each skill group (bold-led paragraph under `## Skills`) → one chunk.
    - Certifications list → one chunk per bullet.
    """
    chunks: list[CVChunk] = []
    current_section = "summary"
    current_company: str | None = None
    role_header_buffer: list[str] = []

    lines = cv_markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if line.startswith("## "):
            current_section = line[3:].strip().lower().split()[0]  # "Experience" → "experience"
            current_company = None
            role_header_buffer = []
            i += 1
            continue

        if line.startswith("### "):
            # Experience role header: "### Title — Company"
            header = line[4:].strip()
            # parse "Role — Company"
            m = re.split(r"\s+[—-]\s+", header, maxsplit=1)
            current_company = (m[1].strip() if len(m) == 2 else header).lower().split()[0]
            role_header_buffer = [header]
            # look ahead for the date line in italics
            if i + 1 < len(lines) and lines[i + 1].strip().startswith("*"):
                role_header_buffer.append(lines[i + 1].strip("* ").strip())
                i += 1
            chunks.append(CVChunk(
                chunk_id=f"{current_section}.{current_company}.header",
                section=current_section,
                company=current_company,
                text=" | ".join(role_header_buffer),
                tech_tags=_extract_tags(" ".join(role_header_buffer)),
            ))
            i += 1
            continue

        if line.startswith("- "):
            text = line[2:].strip()
            # continuation lines (indented)
            while i + 1 < len(lines) and lines[i + 1].startswith("  "):
                text += " " + lines[i + 1].strip()
                i += 1
            bullet_idx = sum(
                1 for c in chunks
                if c.section == current_section and c.company == current_company
                and not c.chunk_id.endswith(".header")
            )
            chunk_id_company = current_company or "general"
            chunks.append(CVChunk(
                chunk_id=f"{current_section}.{chunk_id_company}.b{bullet_idx}",
                section=current_section,
                company=current_company,
                text=text,
                tech_tags=_extract_tags(text),
            ))
            i += 1
            continue

        if line.startswith("**") and current_section == "skills":
            # "**Generative AI & NLP** — …"
            m = re.match(r"\*\*(.+?)\*\*\s*[—-]?\s*(.*)", line)
            if m:
                group, body = m.groups()
                # consume continuation lines
                while i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith(("**", "##", "###", "- ")):
                    body += " " + lines[i + 1].strip()
                    i += 1
                slug = re.sub(r"\W+", "_", group.strip().lower()).strip("_")
                chunks.append(CVChunk(
                    chunk_id=f"skills.{slug}",
                    section="skills",
                    text=f"{group}: {body}",
                    tech_tags=_extract_tags(body),
                ))
            i += 1
            continue

        # summary paragraph(s)
        if current_section == "summary" and line.strip():
            text = line.strip()
            while i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith(("##", "###", "---")):
                text += " " + lines[i + 1].strip()
                i += 1
            if text:
                chunks.append(CVChunk(
                    chunk_id=f"summary.{len([c for c in chunks if c.section == 'summary'])}",
                    section="summary",
                    text=text,
                    tech_tags=_extract_tags(text),
                ))
            i += 1
            continue

        i += 1

    return chunks


# ── FAISS index ──────────────────────────────────────────────────────

@dataclass
class CVIndex:
    """A FAISS index + sidecar metadata mapping vector-id → CVChunk."""

    index: "faiss.Index"            # noqa: F821 (runtime import)
    chunks: list[CVChunk]
    model_name: str

    def search(self, queries: list[str], top_k: int = 3) -> list[list[RetrievalHit]]:
        import faiss  # local import
        from sentence_transformers import SentenceTransformer

        model = _get_embedder(self.model_name)
        qvec = model.encode(queries, normalize_embeddings=True).astype("float32")
        scores, ids = self.index.search(qvec, top_k)
        hits: list[list[RetrievalHit]] = []
        for row_scores, row_ids, req_text in zip(scores, ids, queries):
            row_hits: list[RetrievalHit] = []
            for s, idx in zip(row_scores, row_ids):
                if idx < 0:
                    continue
                row_hits.append(RetrievalHit(
                    chunk=self.chunks[idx],
                    score=float(s),
                    matched_requirement=req_text,
                ))
            hits.append(row_hits)
        return hits


_embedder_cache: dict[str, object] = {}


def _get_embedder(model_name: str):
    from sentence_transformers import SentenceTransformer

    if model_name not in _embedder_cache:
        _embedder_cache[model_name] = SentenceTransformer(model_name)
    return _embedder_cache[model_name]


def build_index(cv_path: Path | None = None) -> CVIndex:
    """Chunk cv.md and (re)build the FAISS index. Persists to disk."""
    import faiss

    s = settings()
    cv_path = cv_path or s.cv_path
    cv_text = cv_path.read_text(encoding="utf-8")
    chunks = _chunk_cv(cv_text)
    if not chunks:
        raise ValueError(f"No chunks produced from {cv_path}")

    embedder = _get_embedder(s.embedding_model)
    texts = [c.text for c in chunks]
    vecs = embedder.encode(texts, normalize_embeddings=True).astype("float32")

    dim = vecs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(vecs)

    s.faiss_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(s.faiss_path))
    s.faiss_meta_path.write_text(
        json.dumps({
            "model": s.embedding_model,
            "chunks": [c.model_dump() for c in chunks],
        }, indent=2),
        encoding="utf-8",
    )
    return CVIndex(index=index, chunks=chunks, model_name=s.embedding_model)


def load_index() -> CVIndex:
    import faiss

    s = settings()
    if not s.faiss_path.exists() or not s.faiss_meta_path.exists():
        return build_index()
    # auto-rebuild if cv.md is newer than the index
    if s.cv_path.stat().st_mtime > s.faiss_path.stat().st_mtime:
        return build_index()
    index = faiss.read_index(str(s.faiss_path))
    meta = json.loads(s.faiss_meta_path.read_text(encoding="utf-8"))
    chunks = [CVChunk.model_validate(c) for c in meta["chunks"]]
    return CVIndex(index=index, chunks=chunks, model_name=meta["model"])


def retrieve_for_requirements(
    requirements: Iterable[str], top_k: int = 3
) -> list[list[RetrievalHit]]:
    """Convenience: given JD requirements, return top-k matching CV chunks each."""
    idx = load_index()
    return idx.search(list(requirements), top_k=top_k)
