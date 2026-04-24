"""Streamlit dashboard.

Talks to the FastAPI service at CAREER_OPS_API_URL (default
http://localhost:8000). If the API is not reachable, we fall back to
direct DB access so the dashboard still works standalone — the API is
optional.

Tabs:
  1. Pipeline     — kanban of Applications by status
  2. Evaluations  — filterable table with drill-down
  3. Stats        — grade + visa distribution, pipeline funnel
  4. Rubric       — inspect one evaluation's per-dim scores + citations

Run:
  career-ops dash       # starts streamlit
  career-ops serve      # starts fastapi (in a separate terminal)
"""

from __future__ import annotations

import os
import sys
from typing import Any

# Ensure the package is importable when Streamlit Cloud runs this file directly
_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

import httpx
import streamlit as st


API_URL = os.environ.get("CAREER_OPS_API_URL", "http://localhost:8000")
API_TIMEOUT = float(os.environ.get("CAREER_OPS_API_TIMEOUT", "5.0"))


# ── Thin data access layer ──────────────────────────────────────────
# Prefer the API; fall back to direct DB calls if unavailable.

def _api_get(path: str, **params) -> Any:
    try:
        r = httpx.get(f"{API_URL}{path}", params=params or None, timeout=API_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _api_post(path: str, payload: dict | None = None) -> Any:
    try:
        r = httpx.post(f"{API_URL}{path}", json=payload, timeout=120.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _fetch_stats() -> dict[str, Any]:
    data = _api_get("/stats")
    if data is not None:
        return data
    try:
        from career_ops import storage
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
            return {
                "jobs_total": jobs_total,
                "evaluations_total": evals_total,
                "grade_distribution": grade_dist,
                "visa_distribution": visa_dist,
                "applications_by_status": status_dist,
            }
    except Exception:
        pass
    return {}


def _fetch_evaluations(**filters) -> list[dict[str, Any]]:
    data = _api_get("/evaluations", **{k: v for k, v in filters.items() if v})
    if data is not None:
        return data
    try:
        from career_ops import storage
        storage.init_db()
        for s in storage.session():
            q = (
                s.query(storage.EvaluationRow, storage.Job, storage.Company)
                 .join(storage.Job, storage.EvaluationRow.job_id == storage.Job.id)
                 .join(storage.Company, storage.Job.company_id == storage.Company.id)
                 .order_by(storage.EvaluationRow.percent.desc())
            )
            if filters.get("grade"):
                q = q.filter(storage.EvaluationRow.grade.in_(filters["grade"].split(",")))
            if filters.get("company"):
                q = q.filter(storage.Company.name.ilike(f"%{filters['company']}%"))
            if filters.get("h1b"):
                q = q.filter(storage.Company.h1b_history == filters["h1b"])
            rows = q.limit(filters.get("limit", 100)).all()
            return [
                {
                    "id": r.EvaluationRow.id,
                    "job_id": r.Job.id,
                    "company": r.Company.name,
                    "title": r.Job.title,
                    "location": r.Job.location,
                    "h1b_history": r.Company.h1b_history,
                    "grade": r.EvaluationRow.grade,
                    "percent": r.EvaluationRow.percent,
                    "created_at": r.EvaluationRow.created_at.isoformat(),
                }
                for r in rows
            ]
    except Exception:
        pass
    return []


def _fetch_evaluation(evaluation_id: int) -> dict[str, Any] | None:
    data = _api_get(f"/evaluations/{evaluation_id}")
    if data is not None:
        return data
    try:
        from career_ops import storage
        for s in storage.session():
            row = (
                s.query(storage.EvaluationRow, storage.Job, storage.Company)
                 .join(storage.Job, storage.EvaluationRow.job_id == storage.Job.id)
                 .join(storage.Company, storage.Job.company_id == storage.Company.id)
                 .filter(storage.EvaluationRow.id == evaluation_id)
                 .one_or_none()
            )
            if not row:
                return None
            return {
                "id": row.EvaluationRow.id,
                "job_id": row.Job.id,
                "company": row.Company.name,
                "title": row.Job.title,
                "location": row.Job.location,
                "h1b_history": row.Company.h1b_history,
                "grade": row.EvaluationRow.grade,
                "percent": row.EvaluationRow.percent,
                "dimension_scores": row.EvaluationRow.scores_json,
                "model": row.EvaluationRow.model,
                "rubric_version": row.EvaluationRow.rubric_version,
            }
    except Exception:
        pass
    return None


# ── Rendering ───────────────────────────────────────────────────────

GRADE_COLORS = {
    "A+": "#0a8754", "A": "#2fa660", "B+": "#77a300",
    "B": "#b4a400", "C": "#c57a00", "D": "#b24a00", "F": "#a11010",
}


def main() -> None:
    st.set_page_config(page_title="career-ops", layout="wide", page_icon=":briefcase:")
    st.title("career-ops · AI Job Search")
    st.caption("Paste a job URL to get an AI-grounded evaluation against your resume.")

    tab_eval, tab_stats, tab_evals, tab_pipeline, tab_rubric = st.tabs(
        ["Evaluate a Job", "Stats", "Evaluations", "Pipeline", "Rubric inspector"]
    )

    with tab_eval:
        _render_evaluate_job()

    with tab_stats:
        _render_stats()

    with tab_evals:
        _render_evaluations()

    with tab_pipeline:
        _render_pipeline()

    with tab_rubric:
        _render_rubric_inspector()


def _render_evaluate_job() -> None:
    st.subheader("Paste a job URL or description")

    url = st.text_input(
        "Job URL",
        placeholder="https://boards.greenhouse.io/anthropic/jobs/...",
        label_visibility="collapsed",
    )

    with st.expander("Or paste raw job description text instead"):
        jd_text = st.text_area(
            "Job description", height=220,
            placeholder="Paste the full job description here if you don't have a URL...",
            label_visibility="collapsed",
        )

    evaluate_btn = st.button("Evaluate this job", type="primary", use_container_width=True)

    if not evaluate_btn:
        st.markdown(
            "<div style='text-align:center;color:#888;margin-top:40px;font-size:14px'>"
            "Paste a URL above and click Evaluate — results appear here in ~30 seconds."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    if not url and not jd_text:
        st.warning("Please enter a job URL or paste the job description text.")
        return

    with st.status("Running evaluation…", expanded=True) as status:
        # ── Step 1: ingest ──────────────────────────────────────────
        st.write("Step 1 / 2 — Ingesting job…")
        ingest_payload = {"url": url} if url else {"text": jd_text}
        ingest_result = _api_post("/ingest", ingest_payload)

        if ingest_result is None:
            # Fallback: call Python directly
            try:
                from career_ops.ingest import ingest_job
                from career_ops import storage
                storage.init_db()
                job = ingest_job(url=url or None, text=jd_text or None)
                ingest_result = {"job_id": job.id, "title": job.title}
            except Exception as exc:
                status.update(label="Ingest failed", state="error")
                st.error(f"Could not ingest the job: {exc}")
                st.info("Make sure career-ops is running locally (`career-ops serve`) or that ANTHROPIC_API_KEY is set.")
                return

        job_id = ingest_result.get("job_id") or ingest_result.get("id")
        title = ingest_result.get("title", f"Job #{job_id}")
        st.write(f"Ingested: **{title}** (job id `{job_id}`)")

        # ── Step 2: evaluate ────────────────────────────────────────
        st.write("Step 2 / 2 — Scoring against your resume…")
        eval_result = _api_post(f"/evaluate/{job_id}")

        if eval_result is None:
            try:
                from career_ops.evaluator import evaluate_job
                ev = evaluate_job(job_id)
                eval_result = {
                    "grade": ev.grade,
                    "percent": ev.percent,
                    "company": ev.company,
                    "title": ev.title,
                    "location": ev.location,
                    "h1b_history": ev.h1b_history,
                    "model": ev.model,
                    "rubric_version": ev.rubric_version,
                    "dimension_scores": [d.dict() for d in ev.dimension_scores],
                }
            except Exception as exc:
                status.update(label="Evaluation failed", state="error")
                st.error(f"Could not evaluate: {exc}")
                return

        status.update(label="Done!", state="complete")

    _display_eval_result(eval_result)


def _display_eval_result(ev: dict) -> None:
    grade = ev.get("grade", "?")
    percent = float(ev.get("percent", 0))
    color = GRADE_COLORS.get(grade, "#555")

    st.markdown(
        f"<div style='background:{color};color:white;border-radius:16px;"
        f"padding:28px;text-align:center;margin:20px 0'>"
        f"<div style='font-size:64px;font-weight:900;line-height:1'>{grade}</div>"
        f"<div style='font-size:20px;margin-top:8px;opacity:0.9'>{percent:.1f}%</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("Company", ev.get("company") or "—")
    c2.metric("Location", ev.get("location") or "—")
    c3.metric("H-1B history", ev.get("h1b_history") or "—")

    st.caption(
        f"**{ev.get('title', '')}** · model: `{ev.get('model', '—')}` "
        f"· rubric: `{ev.get('rubric_version', '—')}`"
    )

    dims = ev.get("dimension_scores") or []
    if dims:
        st.subheader("Dimension Breakdown")
        for d in sorted(dims, key=lambda x: x.get("score", 0), reverse=True):
            score = d.get("score", 0)
            icon = "🟢" if score >= 4 else "🟡" if score >= 3 else "🔴"
            with st.expander(f"{icon} **{d.get('dimension_id', '')}** — {score}/5",
                             expanded=score >= 4 or score <= 1):
                st.write(d.get("reasoning", ""))
                if d.get("citations"):
                    st.caption("Cited resume chunks:")
                    for cid in d["citations"]:
                        st.code(cid, language=None)


def _render_stats() -> None:
    stats = _fetch_stats()
    if not stats:
        st.info("No data yet. Run `career-ops ingest` + `evaluate` to populate.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Jobs ingested", stats.get("jobs_total", 0))
    c2.metric("Evaluations", stats.get("evaluations_total", 0))
    total_apps = sum((stats.get("applications_by_status") or {}).values())
    c3.metric("Applications", total_apps)

    st.subheader("Grade distribution")
    grade_dist = stats.get("grade_distribution") or {}
    if grade_dist:
        order = ["A+", "A", "B+", "B", "C", "D", "F"]
        rows = [{"grade": g, "count": grade_dist.get(g, 0)} for g in order]
        st.bar_chart(rows, x="grade", y="count")
    else:
        st.caption("No evaluations yet.")

    st.subheader("Visa sponsor history of evaluated roles")
    visa = stats.get("visa_distribution") or {}
    if visa:
        order = ["heavy", "active", "occasional", "unknown", "none"]
        rows = [{"h1b_history": k, "count": visa.get(k, 0)} for k in order]
        st.bar_chart(rows, x="h1b_history", y="count")
    else:
        st.caption("No evaluations yet.")


def _render_evaluations() -> None:
    st.subheader("Evaluations")

    # Filters
    col1, col2, col3, col4 = st.columns(4)
    grade_multi = col1.multiselect("Grade", ["A+", "A", "B+", "B", "C", "D", "F"], default=[])
    company_q = col2.text_input("Company contains")
    h1b_sel = col3.selectbox("h1b_history",
                             ["", "heavy", "active", "occasional", "unknown", "none"])
    min_pct = col4.slider("Min percent", 0, 100, 0)

    rows = _fetch_evaluations(
        grade=",".join(grade_multi) if grade_multi else None,
        company=company_q or None,
        h1b=h1b_sel or None,
        min_percent=min_pct if min_pct > 0 else None,
    )

    if not rows:
        st.info("No evaluations match the current filters.")
        return

    # Color-coded display
    for r in rows:
        color = GRADE_COLORS.get(r["grade"], "#333")
        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 6, 2])
            c1.markdown(
                f"<div style='background:{color};color:white;padding:8px;text-align:center;"
                f"border-radius:6px;font-weight:700;font-size:18px'>{r['grade']}</div>",
                unsafe_allow_html=True,
            )
            c2.markdown(
                f"**{r['title']}** — *{r['company']}*  \n"
                f"{r['location'] or 'unknown'} · h1b={r['h1b_history']}"
            )
            c3.markdown(f"**{r['percent']:.1f}%**  \n`eval_id={r['id']}`")


def _render_pipeline() -> None:
    stats = _fetch_stats()
    by_status = (stats.get("applications_by_status") or {}) if stats else {}
    cols = st.columns(6)
    for col, status in zip(cols, ["interested", "applied", "phone", "onsite", "offer", "rejected"]):
        with col:
            st.metric(status.title(), by_status.get(status, 0))
    st.caption(
        "Applications are created manually — e.g. after tailoring a CV. "
        "v1.5 will auto-link tailored CVs to an `interested` application."
    )


def _render_rubric_inspector() -> None:
    evaluation_id = st.number_input("Evaluation id", min_value=1, step=1)
    if not evaluation_id:
        return
    ev = _fetch_evaluation(int(evaluation_id))
    if not ev:
        st.warning("No such evaluation.")
        return

    color = GRADE_COLORS.get(ev["grade"], "#333")
    st.markdown(
        f"### <span style='color:{color}'>{ev['grade']} / {ev['percent']:.1f}%</span> — "
        f"{ev['title']} @ {ev['company']}",
        unsafe_allow_html=True,
    )
    st.caption(f"model: `{ev['model']}` · rubric: `{ev['rubric_version']}` · "
               f"h1b: `{ev['h1b_history']}`")

    # Dimension scores sorted high→low
    dims = sorted(ev["dimension_scores"], key=lambda d: d["score"], reverse=True)
    for d in dims:
        hdr = f"**{d['dimension_id']}** — {d['score']}/5"
        with st.expander(hdr, expanded=d["score"] >= 4 or d["score"] <= 1):
            st.write(d["reasoning"])
            if d.get("citations"):
                st.caption("Cited CV chunks:")
                for cid in d["citations"]:
                    st.code(cid, language=None)


if __name__ == "__main__":
    main()
