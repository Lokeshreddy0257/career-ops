"""Typer CLI entrypoint — `career-ops ...`."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import batch as _batch
from . import evaluator as _evaluator
from . import ingest as _ingest
from . import portals as _portals
from . import storage as _storage
from . import tailor as _tailor
from .config import load_rubric
from .embeddings import build_index


app = typer.Typer(add_completion=False, no_args_is_help=True, help="career-ops CLI")
console = Console()


# ── ingest ───────────────────────────────────────────────────────────

@app.command()
def ingest(
    source: str = typer.Argument(..., help="URL, path to .md/.txt, or raw JD text in quotes"),
    source_kind: str = typer.Option("auto", help="auto|url|text"),
    json_out: bool = typer.Option(False, "--json", help="Print JSON only"),
) -> None:
    """Ingest a JD and persist it as a Job row."""
    job_id = _ingest.ingest(source, source_kind=source_kind)  # type: ignore[arg-type]
    if json_out:
        typer.echo(json.dumps({"job_id": job_id}))
    else:
        console.print(f"[green]Ingested[/green] → job_id=[bold]{job_id}[/bold]")


# ── evaluate ─────────────────────────────────────────────────────────

@app.command()
def evaluate(
    job_id: int = typer.Argument(..., help="jobs.id to evaluate"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Score a job against the rubric with RAG-grounded citations."""
    ev = _evaluator.evaluate_job(job_id)
    if json_out:
        typer.echo(ev.model_dump_json(indent=2))
        return
    console.print(f"[bold]{ev.grade}[/bold] / {ev.percent}%  —  {ev.title} @ {ev.company} ({ev.location})")
    console.print(f"  model={ev.model}  rubric={ev.rubric_version}")
    for d in ev.dimension_scores:
        cits = ", ".join(d.citations) if d.citations else "-"
        console.print(f"  • [cyan]{d.dimension_id}[/cyan]: {d.score}/5 — {d.reasoning} [{cits}]")


# ── tailor ───────────────────────────────────────────────────────────

@app.command()
def tailor(evaluation_id: int) -> None:
    """Generate a tailored CV PDF for an evaluation."""
    result = _tailor.tailor_for_evaluation(evaluation_id)
    console.print(f"[green]Tailored CV →[/green] {result.pdf_path}")
    console.print(f"Emphasized tags: {', '.join(result.emphasis_tags)}")
    for b in result.bullets[:5]:
        console.print(f"  • [{b.source_chunk_id}] {b.rewritten[:110]}")


# ── scan ─────────────────────────────────────────────────────────────

@app.command()
def scan(
    company: str = typer.Argument(None, help="Company name from targets.yml; omit with --all"),
    all_: bool = typer.Option(False, "--all"),
    limit: int = typer.Option(20),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Scan one company or all targets for new postings."""
    if all_:
        results = _portals.scan_all(limit_per_company=limit)
    else:
        if not company:
            raise typer.BadParameter("Provide a company name or pass --all")
        results = [_portals.scan_company(company)]
    if json_out:
        typer.echo(json.dumps([r.__dict__ for r in results], default=str))
        return
    tbl = Table("company", "new_jobs", "dupes", "errors")
    for r in results:
        tbl.add_row(
            r.company,
            str(len(r.new_job_ids)),
            str(r.skipped_duplicates),
            "; ".join(r.errors) or "-",
        )
    console.print(tbl)


# ── batch ────────────────────────────────────────────────────────────

@app.command()
def batch(
    ids: str = typer.Option(None, "--ids", help="Comma-separated job ids"),
    unevaluated: bool = typer.Option(False, "--unevaluated"),
    limit: int = typer.Option(10),
    concurrency: int = typer.Option(5),
) -> None:
    """Evaluate many jobs in parallel."""
    if ids:
        job_ids = [int(x) for x in ids.split(",") if x.strip()]
    elif unevaluated:
        job_ids = _batch.unevaluated_job_ids(limit=limit)
    else:
        raise typer.BadParameter("Pass --ids or --unevaluated")
    results = asyncio.run(_batch.run_batch(job_ids, concurrency=concurrency))
    tbl = Table("job_id", "eval_id", "grade", "percent", "error")
    for r in sorted(results, key=lambda x: (x.percent or 0), reverse=True):
        tbl.add_row(
            str(r.job_id),
            str(r.evaluation_id or "-"),
            r.grade or "-",
            f"{r.percent:.1f}" if r.percent is not None else "-",
            r.error or "-",
        )
    console.print(tbl)


# ── list-evaluations ─────────────────────────────────────────────────

@app.command("list-evaluations")
def list_evaluations(limit: int = 10, json_out: bool = typer.Option(False, "--json")) -> None:
    _storage.init_db()
    for s in _storage.session():
        rows = (
            s.query(_storage.EvaluationRow, _storage.Job, _storage.Company)
             .join(_storage.Job, _storage.EvaluationRow.job_id == _storage.Job.id)
             .join(_storage.Company, _storage.Job.company_id == _storage.Company.id)
             .order_by(_storage.EvaluationRow.created_at.desc())
             .limit(limit)
             .all()
        )
    data = [
        {
            "eval_id": r.EvaluationRow.id,
            "grade": r.EvaluationRow.grade,
            "percent": r.EvaluationRow.percent,
            "company": r.Company.name,
            "title": r.Job.title,
        }
        for r in rows
    ]
    if json_out:
        typer.echo(json.dumps(data))
        return
    tbl = Table("eval_id", "grade", "%", "company", "title")
    for d in data:
        tbl.add_row(str(d["eval_id"]), d["grade"], f"{d['percent']:.1f}", d["company"], d["title"])
    console.print(tbl)


# ── profile ──────────────────────────────────────────────────────────

profile_app = typer.Typer(help="Manage cv.md and config/*.yml")
app.add_typer(profile_app, name="profile")


@profile_app.command("reindex")
def profile_reindex() -> None:
    """Rebuild the FAISS index over cv.md."""
    idx = build_index()
    console.print(f"[green]Index rebuilt[/green] with {len(idx.chunks)} chunks "
                  f"using {idx.model_name}")


@profile_app.command("validate")
def profile_validate() -> None:
    """Validate YAML configs parse and rubric is internally consistent."""
    from .config import Rubric
    rubric = Rubric.current()
    console.print(f"[green]rubric[/green] v{rubric.version} "
                  f"({len(rubric.dimensions)} dimensions, Σweight={rubric.total_weight:.2f})")
    load_rubric()
    console.print("[green]profile.yml / targets.yml / portals.yml parse OK[/green]")


# ── calibrate ────────────────────────────────────────────────────────

@app.command()
def calibrate(
    mode: str = typer.Option(
        "reference",
        help="'reference' (deterministic, no LLM) or 'live' (calls the LLM).",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the offline calibration harness against labeled JD fixtures."""
    from .calibration import run_calibration  # lazy import — pulls pydantic

    if mode not in ("reference", "live"):
        raise typer.BadParameter("mode must be 'reference' or 'live'")
    report = run_calibration(mode=mode)  # type: ignore[arg-type]
    if json_out:
        import json as _json
        typer.echo(_json.dumps(report.__dict__, default=lambda o: o.__dict__, indent=2))
    else:
        console.print(report.summary())


# ── prep ─────────────────────────────────────────────────────────────

@app.command()
def prep(
    evaluation_id: int,
    save: bool = typer.Option(True, help="Save rendered markdown under artifacts/"),
) -> None:
    """Generate STAR-structured interview prep for an evaluation."""
    from .prep import prep_for_evaluation, render_markdown
    from datetime import date
    from .config import settings
    pack = prep_for_evaluation(evaluation_id)
    md = render_markdown(pack)
    console.print(md)
    if save:
        settings().artifacts_dir.mkdir(parents=True, exist_ok=True)
        slug = pack.company.lower().replace(" ", "_").replace("/", "_")
        out = settings().artifacts_dir / f"prep_{slug}_{date.today().isoformat()}.md"
        out.write_text(md, encoding="utf-8")
        console.print(f"\n[green]Saved →[/green] {out}")


# ── serve ────────────────────────────────────────────────────────────

@app.command()
def serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev)"),
) -> None:
    """Run the FastAPI service at career_ops.api:app."""
    import uvicorn
    uvicorn.run("career_ops.api:app", host=host, port=port, reload=reload)


# ── dash ─────────────────────────────────────────────────────────────

@app.command()
def dash() -> None:
    """Launch the Streamlit dashboard."""
    here = Path(__file__).resolve()
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(here.parent / "dashboard.py")],
        check=False,
    )


if __name__ == "__main__":
    app()
