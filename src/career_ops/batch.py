"""Async batch evaluator: process many jobs in parallel with bounded concurrency."""

from __future__ import annotations

import asyncio
import concurrent.futures
from dataclasses import dataclass

import structlog

from .evaluator import evaluate_job
from . import storage


log = structlog.get_logger(__name__)


@dataclass
class BatchResult:
    evaluation_id: int | None
    job_id: int
    grade: str | None
    percent: float | None
    error: str | None = None


async def run_batch(job_ids: list[int], *, concurrency: int = 5) -> list[BatchResult]:
    """Evaluate each job id concurrently. `evaluate_job` is sync + IO-heavy
    (LLM calls), so we run it in a thread pool."""
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(concurrency)

    async def _one(job_id: int) -> BatchResult:
        async with sem:
            try:
                ev = await loop.run_in_executor(None, evaluate_job, job_id)
                # find the row id we just wrote
                for s in storage.session():
                    row = (
                        s.query(storage.EvaluationRow)
                         .filter_by(job_id=job_id)
                         .order_by(storage.EvaluationRow.id.desc())
                         .first()
                    )
                    return BatchResult(
                        evaluation_id=row.id if row else None,
                        job_id=job_id,
                        grade=ev.grade,
                        percent=ev.percent,
                    )
                return BatchResult(evaluation_id=None, job_id=job_id, grade=ev.grade, percent=ev.percent)
            except Exception as e:
                log.warning("batch.job_failed", job_id=job_id, err=str(e))
                return BatchResult(evaluation_id=None, job_id=job_id, grade=None, percent=None, error=str(e))

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        loop.set_default_executor(ex)
        return await asyncio.gather(*(_one(jid) for jid in job_ids))


def unevaluated_job_ids(limit: int = 20) -> list[int]:
    storage.init_db()
    for s in storage.session():
        rows = (
            s.query(storage.Job)
             .outerjoin(storage.EvaluationRow)
             .filter(storage.EvaluationRow.id.is_(None))
             .limit(limit)
             .all()
        )
        return [r.id for r in rows]
    return []
