"""FastAPI tests using TestClient + an isolated SQLite DB.

These tests hit the endpoints that don't need the LLM:
  - /healthz
  - /stats
  - /evaluations (read path, after we seed a row directly via storage)
  - /jobs (read path)

The write paths that call the LLM (POST /jobs for URL ingest, POST
/evaluations, POST /tailor, POST /prep) are not exercised here — those
live in integration tests not run in CI.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the engine at an isolated SQLite file for this test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("CAREER_OPS_DATABASE_URL", f"sqlite:///{db_path}")
    # Invalidate the cached settings / engine so the new env is picked up.
    from career_ops import config as _config
    from career_ops import storage as _storage
    _config.settings.cache_clear()
    _storage._engine.cache_clear()
    _storage._session_factory.cache_clear()
    _storage.init_db()
    return db_path


@pytest.fixture
def client(isolated_db):
    from fastapi.testclient import TestClient
    from career_ops.api import create_app
    return TestClient(create_app())


def test_healthz(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "rubric_version" in body


def test_stats_empty_db(client) -> None:
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["jobs_total"] == 0
    assert body["evaluations_total"] == 0
    assert body["grade_distribution"] == {}


def test_jobs_empty(client) -> None:
    r = client.get("/jobs")
    assert r.status_code == 200
    assert r.json() == []


def test_evaluations_empty(client) -> None:
    r = client.get("/evaluations")
    assert r.status_code == 200
    assert r.json() == []


def test_evaluations_404_on_missing_id(client) -> None:
    r = client.get("/evaluations/9999")
    assert r.status_code == 404


def test_stats_with_seeded_rows(client, isolated_db) -> None:
    """Seed the DB directly and confirm /stats aggregates correctly."""
    from datetime import datetime
    from career_ops import storage
    for s in storage.session():
        c = storage.Company(name="TestCo", h1b_history="heavy")
        s.add(c); s.flush()
        j = storage.Job(
            company_id=c.id, title="Sr ML Eng", location="Remote",
            jd_text="...", source_kind="text", ingested_at=datetime.utcnow(),
        )
        s.add(j); s.flush()
        e = storage.EvaluationRow(
            job_id=j.id, rubric_version="test", model="test",
            scores_json=[{"dimension_id": "x", "score": 5, "reasoning": "", "citations": []}],
            weighted_total=9.2, percent=92.0, grade="A+",
        )
        s.add(e)
        s.add(storage.Application(job_id=j.id, status="applied"))
        s.commit()

    r = client.get("/stats")
    body = r.json()
    assert body["jobs_total"] == 1
    assert body["evaluations_total"] == 1
    assert body["grade_distribution"].get("A+") == 1
    assert body["visa_distribution"].get("heavy") == 1
    assert body["applications_by_status"].get("applied") == 1


def test_evaluations_grade_filter(client, isolated_db) -> None:
    from career_ops import storage
    for s in storage.session():
        c = storage.Company(name="TestCo", h1b_history="active"); s.add(c); s.flush()
        j = storage.Job(company_id=c.id, title="t", jd_text=".", source_kind="text"); s.add(j); s.flush()
        s.add(storage.EvaluationRow(job_id=j.id, rubric_version="t", model="t",
                                    scores_json=[], weighted_total=9, percent=92, grade="A+"))
        s.add(storage.EvaluationRow(job_id=j.id, rubric_version="t", model="t",
                                    scores_json=[], weighted_total=6, percent=65, grade="C"))
        s.commit()

    r = client.get("/evaluations", params={"grade": "A+"})
    data = r.json()
    assert len(data) == 1
    assert data[0]["grade"] == "A+"

    r = client.get("/evaluations", params={"h1b": "active"})
    assert len(r.json()) == 2

    r = client.get("/evaluations", params={"min_percent": 80})
    data = r.json()
    assert all(row["percent"] >= 80 for row in data)
