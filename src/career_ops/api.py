"""FastAPI service layer.

Exposes the engine as HTTP endpoints so the dashboard, external
tooling, and Claude Code can all talk to the same API. This is the
service architecture I'd ship in production at a company — not because
we need multi-client access here, but because it's the clean
separation I'm showing off.

Endpoints:
  GET   /healthz             liveness
  GET   /stats               pipeline + grade distribution
  GET   /jobs                list ingested jobs (paged, filterable)
  POST  /jobs                ingest a JD from URL / text / path
  GET   /jobs/{job_id}       one job
  GET   /evaluations         list evaluations (filterable by grade / company / h1b)
  POST  /evaluations         score a job_id under the current rubric
  GET   /evaluations/{id}    one evaluation (with per-dim scores + citations)
  POST  /tailor/{eval_id}    generate a tailored CV PDF for an evaluation
  POST  /prep/{eval_id}      generate interview prep for an evaluation

OpenAPI docs auto-generated at /docs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import __version__


# ── Response models (kept separate from SQLA models) ─────────────────

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    rubric_version: str


class IngestRequest(BaseModel):
    source: str = Field(..., description="URL, path to .md/.txt, or raw JD text")
    source_kind: Literal["auto", "url", "text"] = "auto"


class JobOut(BaseModel):
    id: int
    company: str
    title: str
    location: str | None
    url: str | None
    source_kind: str
    ingested_at: datetime
    evaluation_count: int = 0


class DimensionScoreOut(BaseModel):
    dimension_id: str
    score: int
    reasoning: str
    citations: list[str] = []


class EvaluationOut(BaseModel):
    id: int
    job_id: int
    company: str
    title: str
    location: str | None
    h1b_history: str
    grade: str
    percent: float
    weighted_total: float
    rubric_version: str
    model: str
    created_at: datetime
    dimension_scores: list[DimensionScoreOut]


class EvaluationSummary(BaseModel):
    id: int
    job_id: int
    company: str
    title: str
    location: str | None
    h1b_history: str
    grade: str
    percent: float
    created_at: datetime


class StatsResponse(BaseModel):
    jobs_total: int
    evaluations_total: int
    grade_distribution: dict[str, int]
    visa_distribution: dict[str, int]        # h1b_history → count of evals
    applications_by_status: dict[str, int]


class TailorResponse(BaseModel):
    evaluation_id: int
    pdf_path: str
    emphasis_tags: list[str]
    bullet_count: int


class PrepResponse(BaseModel):
    evaluation_id: int
    stories: list[dict[str, Any]]


# ── App factory ──────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """Create the FastAPI app. Factory pattern so tests can spin up
    the app against an isolated DB without module-level side effects."""
    app = FastAPI(
        title="career-ops",
        version=__version__,
        description=(
            "AI-powered, RAG-grounded job-search system. "
            "See /docs for the full OpenAPI spec."
        ),
    )

    # ── Healthz ─────────────────────────────────────────────────────
    @app.get("/healthz", response_model=HealthResponse, tags=["system"])
    def healthz() -> HealthResponse:
        from .config import Rubric
        return HealthResponse(
            version=__version__,
            rubric_version=Rubric.current().version,
        )

    # ── Stats ───────────────────────────────────────────────────────
    @app.get("/stats", response_model=StatsResponse, tags=["system"])
    def stats() -> StatsResponse:
        from . import storage
        storage.init_db()
        for s in storage.session():
            jobs_total = s.query(storage.Job).count()
            evals_total = s.query(storage.EvaluationRow).count()
            grade_dist: dict[str, int] = {}
            for row in s.query(storage.EvaluationRow.grade).all():
                grade_dist[row[0]] = grade_dist.get(row[0], 0) + 1
            visa_dist: dict[str, int] = {}
            rows = (
                s.query(storage.Company.h1b_history, storage.EvaluationRow.id)
                 .join(storage.Job, storage.Company.id == storage.Job.company_id)
                 .join(storage.EvaluationRow, storage.EvaluationRow.job_id == storage.Job.id)
                 .all()
            )
            for h, _ in rows:
                visa_dist[h] = visa_dist.get(h, 0) + 1
            status_dist: dict[str, int] = {}
            for row in s.query(storage.Application.status).all():
                status_dist[row[0]] = status_dist.get(row[0], 0) + 1
            return StatsResponse(
                jobs_total=jobs_total,
                evaluations_total=evals_total,
                grade_distribution=grade_dist,
                visa_distribution=visa_dist,
                applications_by_status=status_dist,
            )
        raise HTTPException(500, "session yielded nothing")

    # ── Jobs ────────────────────────────────────────────────────────
    @app.get("/jobs", response_model=list[JobOut], tags=["jobs"])
    def list_jobs(
        company: str | None = Query(None),
        limit: int = Query(50, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[JobOut]:
        from . import storage
        storage.init_db()
        for s in storage.session():
            q = (
                s.query(storage.Job, storage.Company)
                 .join(storage.Company, storage.Job.company_id == storage.Company.id)
            )
            if company:
                q = q.filter(storage.Company.name.ilike(f"%{company}%"))
            rows = q.order_by(storage.Job.ingested_at.desc()).limit(limit).offset(offset).all()
            # Evaluation counts per job in one query
            ids = [j.Job.id for j in rows]
            counts: dict[int, int] = {}
            if ids:
                for jid, cnt in (
                    s.query(storage.EvaluationRow.job_id,
                            storage.EvaluationRow.id)
                     .filter(storage.EvaluationRow.job_id.in_(ids))
                     .all()
                ):
                    counts[jid] = counts.get(jid, 0) + 1
            return [
                JobOut(
                    id=r.Job.id,
                    company=r.Company.name,
                    title=r.Job.title,
                    location=r.Job.location,
                    url=r.Job.url,
                    source_kind=r.Job.source_kind,
                    ingested_at=r.Job.ingested_at,
                    evaluation_count=counts.get(r.Job.id, 0),
                )
                for r in rows
            ]
        raise HTTPException(500, "session yielded nothing")

    @app.post("/jobs", response_model=JobOut, tags=["jobs"], status_code=201)
    def ingest_job(req: IngestRequest) -> JobOut:
        from . import ingest, storage
        job_id = ingest.ingest(req.source, source_kind=req.source_kind)  # type: ignore[arg-type]
        for s in storage.session():
            row = (
                s.query(storage.Job, storage.Company)
                 .join(storage.Company, storage.Job.company_id == storage.Company.id)
                 .filter(storage.Job.id == job_id).one()
            )
            return JobOut(
                id=row.Job.id, company=row.Company.name,
                title=row.Job.title, location=row.Job.location,
                url=row.Job.url, source_kind=row.Job.source_kind,
                ingested_at=row.Job.ingested_at, evaluation_count=0,
            )
        raise HTTPException(500, "session yielded nothing")

    @app.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
    def get_job(job_id: int) -> JobOut:
        from . import storage
        for s in storage.session():
            row = (
                s.query(storage.Job, storage.Company)
                 .join(storage.Company, storage.Job.company_id == storage.Company.id)
                 .filter(storage.Job.id == job_id).one_or_none()
            )
            if not row:
                raise HTTPException(404, f"No job {job_id}")
            cnt = s.query(storage.EvaluationRow).filter_by(job_id=job_id).count()
            return JobOut(
                id=row.Job.id, company=row.Company.name,
                title=row.Job.title, location=row.Job.location,
                url=row.Job.url, source_kind=row.Job.source_kind,
                ingested_at=row.Job.ingested_at, evaluation_count=cnt,
            )
        raise HTTPException(500, "session yielded nothing")

    # ── Evaluations ─────────────────────────────────────────────────
    @app.get("/evaluations", response_model=list[EvaluationSummary], tags=["evaluations"])
    def list_evaluations(
        grade: str | None = Query(None, description="Comma-separated grades to include"),
        company: str | None = Query(None),
        h1b: str | None = Query(None, description="heavy|active|occasional|unknown|none"),
        min_percent: float | None = Query(None, ge=0, le=100),
        limit: int = Query(100, le=500),
    ) -> list[EvaluationSummary]:
        from . import storage
        storage.init_db()
        allowed_grades = None
        if grade:
            allowed_grades = {g.strip() for g in grade.split(",")}
        for s in storage.session():
            q = (
                s.query(storage.EvaluationRow, storage.Job, storage.Company)
                 .join(storage.Job, storage.EvaluationRow.job_id == storage.Job.id)
                 .join(storage.Company, storage.Job.company_id == storage.Company.id)
            )
            if allowed_grades:
                q = q.filter(storage.EvaluationRow.grade.in_(allowed_grades))
            if company:
                q = q.filter(storage.Company.name.ilike(f"%{company}%"))
            if h1b:
                q = q.filter(storage.Company.h1b_history == h1b)
            if min_percent is not None:
                q = q.filter(storage.EvaluationRow.percent >= min_percent)
            rows = q.order_by(storage.EvaluationRow.percent.desc()).limit(limit).all()
            return [
                EvaluationSummary(
                    id=r.EvaluationRow.id,
                    job_id=r.Job.id,
                    company=r.Company.name,
                    title=r.Job.title,
                    location=r.Job.location,
                    h1b_history=r.Company.h1b_history,
                    grade=r.EvaluationRow.grade,
                    percent=r.EvaluationRow.percent,
                    created_at=r.EvaluationRow.created_at,
                )
                for r in rows
            ]
        raise HTTPException(500, "session yielded nothing")

    @app.post("/evaluations", response_model=EvaluationOut, tags=["evaluations"], status_code=201)
    def create_evaluation(payload: dict) -> EvaluationOut:
        job_id = payload.get("job_id")
        if not isinstance(job_id, int):
            raise HTTPException(422, "body requires integer 'job_id'")
        from . import evaluator
        ev = evaluator.evaluate_job(job_id)
        return _evaluation_to_out(ev_job_id=job_id, evaluation_id=None, use_latest=True)

    @app.get("/evaluations/{evaluation_id}", response_model=EvaluationOut, tags=["evaluations"])
    def get_evaluation(evaluation_id: int) -> EvaluationOut:
        return _evaluation_to_out(ev_job_id=None, evaluation_id=evaluation_id, use_latest=False)

    # ── Tailor ──────────────────────────────────────────────────────
    @app.post("/tailor/{evaluation_id}", response_model=TailorResponse, tags=["artifacts"])
    def tailor(evaluation_id: int) -> TailorResponse:
        from . import tailor as _tailor
        t = _tailor.tailor_for_evaluation(evaluation_id)
        return TailorResponse(
            evaluation_id=evaluation_id,
            pdf_path=t.pdf_path or "",
            emphasis_tags=t.emphasis_tags,
            bullet_count=len(t.bullets),
        )

    # ── Prep ────────────────────────────────────────────────────────
    @app.post("/prep/{evaluation_id}", response_model=PrepResponse, tags=["artifacts"])
    def prep(evaluation_id: int) -> PrepResponse:
        from . import prep as _prep
        r = _prep.prep_for_evaluation(evaluation_id)
        return PrepResponse(
            evaluation_id=evaluation_id,
            stories=[s.model_dump() for s in r.stories],
        )

    return app


# ── Module-level default app for `uvicorn career_ops.api:app` ────────

app = create_app()


# ── Shared eval-serialization helper ─────────────────────────────────

def _evaluation_to_out(*, ev_job_id: int | None, evaluation_id: int | None, use_latest: bool) -> EvaluationOut:
    from . import storage
    storage.init_db()
    for s in storage.session():
        q = (
            s.query(storage.EvaluationRow, storage.Job, storage.Company)
             .join(storage.Job, storage.EvaluationRow.job_id == storage.Job.id)
             .join(storage.Company, storage.Job.company_id == storage.Company.id)
        )
        if evaluation_id is not None:
            q = q.filter(storage.EvaluationRow.id == evaluation_id)
        elif ev_job_id is not None and use_latest:
            q = q.filter(storage.EvaluationRow.job_id == ev_job_id) \
                 .order_by(storage.EvaluationRow.id.desc())
        row = q.first()
        if not row:
            raise HTTPException(404, "Evaluation not found")
        dims = [
            DimensionScoreOut(
                dimension_id=d["dimension_id"],
                score=d["score"],
                reasoning=d["reasoning"],
                citations=d.get("citations", []),
            )
            for d in row.EvaluationRow.scores_json
        ]
        return EvaluationOut(
            id=row.EvaluationRow.id,
            job_id=row.Job.id,
            company=row.Company.name,
            title=row.Job.title,
            location=row.Job.location,
            h1b_history=row.Company.h1b_history,
            grade=row.EvaluationRow.grade,
            percent=row.EvaluationRow.percent,
            weighted_total=row.EvaluationRow.weighted_total,
            rubric_version=row.EvaluationRow.rubric_version,
            model=row.EvaluationRow.model,
            created_at=row.EvaluationRow.created_at,
            dimension_scores=dims,
        )
    raise HTTPException(500, "session yielded nothing")
