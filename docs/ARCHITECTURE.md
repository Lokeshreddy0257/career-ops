# career-ops: Architecture

> An AI-powered, RAG-grounded job-search system for ML / GenAI / MLOps roles.
> Built from scratch in Python around Claude Code skills.

This document describes **what** is in the system, **why** each piece exists,
and **how** data flows through it.

---

## 1. Design goals

1. **Be distinctly mine, not a clone.** The reference project (santifer/career-ops)
   is Node + Go + Playwright + LLM-as-judge. This rebuild is **Python-first**
   (FastAPI, LangChain, sentence-transformers, FAISS, SQLAlchemy, Streamlit,
   WeasyPrint, Playwright-python) because that stack mirrors my production
   experience at Intuit and Bank of America.
2. **Grounded evaluation, not prompt-only.** A job is evaluated with a real
   RAG pipeline: the CV is chunked and embedded into a FAISS index, job
   requirements are retrieved against it, and the LLM is asked to score each
   rubric dimension **with citations to specific CV bullets**. This removes
   hallucinated fit claims and produces defensible scores.
3. **F1 OPT-aware, not OPT-blind.** Visa sponsorship history is a first-class
   scoring dimension (soft signal, weight 1.3). Every target company gets an
   `h1b_history` tag from public LCA data so the rubric can reason about it.
4. **Portfolio-worthy.** The project itself demonstrates the exact skills on
   my resume: RAG, semantic search, FastAPI microservices, async pipelines,
   SQLAlchemy, Docker, CI/CD, evaluation-driven ML.
5. **Engine-agnostic Claude Code integration.** Core logic is a plain Python
   CLI (`career-ops …`). Claude Code skills are thin wrappers that call it.
   Swap the CLI for a different agent runtime without rewriting the engine.

---

## 2. System overview

```
                ┌───────────────────────────────────────────────────────┐
                │                   USER LAYER (you)                    │
                │  cv.md   profile.yml   rubric.yml   targets.yml       │
                └───────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                        CLAUDE CODE SKILL LAYER                            │
│  .claude/skills/{evaluate, tailor-cv, scan-portals, batch, profile}       │
│  Each skill is a markdown file that tells Claude *when* and *how* to      │
│  invoke the Python CLI, and how to present results back to the user.      │
└──────────────────────────────────────────────────────────────────────────┘
                                       │  (invokes)
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          PYTHON ENGINE (src/career_ops)                   │
│                                                                           │
│   ingest  ─▶  embeddings (FAISS) ─▶  evaluator ─▶  ranker                 │
│      │                                    │                               │
│      ▼                                    ▼                               │
│    portals                              tailor ─▶  PDF (WeasyPrint)       │
│      │                                    │                               │
│      ▼                                    ▼                               │
│   storage (SQLAlchemy / SQLite)  ◀───────┘                                │
│      │                                                                    │
│      ▼                                                                    │
│   FastAPI + Streamlit dashboard                                           │
└──────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                          data/career.db  +  artifacts/
```

---

## 3. Components

### 3.1 User layer (`cv.md`, `config/*.yml`)

The *only* files the user edits. Everything downstream is derived.

- **`cv.md`** — canonical resume as Markdown. Parsed into logical sections
  (Summary, Experience.<company>, Skills.<group>, Education, Certifications).
- **`config/profile.yml`** — identity + search preferences: target titles,
  seniority, comp floor, location prefs, visa status, years of experience,
  authorized-to-work-in, requires-sponsorship.
- **`config/rubric.yml`** — 10 weighted scoring dimensions (see §5).
- **`config/targets.yml`** — companies I'd like to work at, each tagged with
  `h1b_history` (sponsor / never / unknown), stage, and portal base URL.
- **`config/portals.yml`** — Playwright selectors per career-site platform
  (Greenhouse / Lever / Ashby / Workday / custom).

### 3.2 Ingestion (`src/career_ops/ingest.py`)

Input: one of
- a pasted job description (text),
- a job URL (fetched via Playwright),
- a Greenhouse / Lever / Ashby job-board JSON endpoint.

Output: a `JobOffer` Pydantic model — company, title, location,
seniority, comp range (if present), full JD text, parsed requirements list,
tech-stack tags, source URL, fetched-at timestamp.

Requirement parsing uses an LLM in structured-output mode (Pydantic schema)
to extract "must-have" vs "nice-to-have" requirements from the JD text.

### 3.3 Embeddings layer (`src/career_ops/embeddings.py`)

- On first run, `cv.md` is chunked (one chunk per bullet + one per section
  header) and embedded with **`sentence-transformers/all-MiniLM-L6-v2`**
  (swap-able to `BAAI/bge-small-en-v1.5` or `text-embedding-3-small`).
- Index stored as a **FAISS `IndexFlatIP`** at `data/cv.faiss` with a
  sidecar JSON mapping vector-id → CV chunk + metadata (section, company,
  tech tags).
- Index is rebuilt automatically when `cv.md`'s mtime changes.

This is lifted straight from the patterns I shipped at Intuit.

### 3.4 Evaluator (`src/career_ops/evaluator.py`)

For each job:

1. **Retrieve.** For each parsed JD requirement, query FAISS top-k=3. This
   gives the evaluator the most relevant CV bullets as grounded context.
2. **Score.** Invoke the LLM (Claude / GPT-4-class) with:
   - the JD,
   - the retrieved CV chunks per requirement,
   - the rubric (dimensions + anchors).
   Response schema: for each dimension, return `{score: 0-5, grade: A-F,
   reasoning: str, citations: [chunk_id]}`.
3. **Visa dimension** is computed *mechanically* first (company's
   `h1b_history` from `targets.yml`) and then narrated by the LLM, so the
   model can't invent sponsorship history that doesn't exist.

Returns a `Evaluation` record persisted to SQLite.

### 3.5 Ranker (`src/career_ops/ranker.py`)

Pure function. Takes a `Evaluation` + `rubric.yml`, computes:
- `weighted_total = Σ (dim.score / 5.0) · dim.weight`,
- `percent = weighted_total / Σ dim.weight · 100`,
- grade mapping `A+ ≥ 90, A ≥ 85, B+ ≥ 78, B ≥ 70, C ≥ 60, D ≥ 50, F < 50`.

### 3.6 Tailor (`src/career_ops/tailor.py`)

Given an `Evaluation` with high enough grade:
1. Pull the top-ranked CV bullets per JD requirement (from the retrieval
   done in 3.4).
2. Ask the LLM to rewrite `cv.md` into a tailored version that reorders and
   lightly rewords bullets to foreground matching content **without
   inventing experience**. Constraint prompt: "Every bullet must be
   traceable to an existing bullet in the source CV."
3. Render Markdown → HTML via a Jinja template → PDF via **WeasyPrint**.
4. Persist artifact under `artifacts/cv_{company}_{yyyy-mm-dd}.pdf`.

### 3.7 Portal scanner (`src/career_ops/portals.py`)

Playwright-python driver over the patterns in `portals.yml`. Each pattern
declares CSS selectors for a job list, pagination, and detail page.
Produces a stream of `JobOffer`s that get piped into the evaluator.

Supported out-of-the-box: Greenhouse, Lever, Ashby, Workday-style pages.

### 3.8 Batch runner (`src/career_ops/batch.py`)

`asyncio` orchestrator: bounded-concurrency (default 5) evaluations in
parallel. Writes each `Evaluation` as it completes so long runs are
resumable. Uses structured logging (`structlog`) and exposes a tqdm
progress bar in CLI mode.

### 3.9 Storage (`src/career_ops/storage.py`)

SQLAlchemy models:
- `Company(id, name, h1b_history, stage, portal_url, notes)`
- `Job(id, company_id, title, url, jd_text, parsed_requirements_json,
  ingested_at)`
- `Evaluation(id, job_id, scores_json, grade, percent, created_at,
  model, rubric_version)`
- `Application(id, job_id, status, applied_at, cv_artifact_path, notes)`
- `CVChunk(id, section, text, embedding_ref)` (metadata only; vectors in
  FAISS).

Default DB: `sqlite:///data/career.db`. Swap to Postgres by changing one
env var.

### 3.10 Dashboard (`src/career_ops/dashboard.py`)

Streamlit app with four tabs. Prefers to read from the FastAPI service
at `CAREER_OPS_API_URL`; falls back to direct DB access if the API is
not reachable.

- **Stats** — grade distribution, visa-history distribution, pipeline
  counters.
- **Evaluations** — filterable list (grade / company / h1b / min %)
  with color-coded grade chips.
- **Pipeline** — kanban counters for Interested / Applied / Phone /
  Onsite / Offer / Rejected.
- **Rubric inspector** — drill into one evaluation, expand each of the
  10 dimensions, see reasoning and the exact `chunk_id`s cited from
  cv.md.

### 3.10a FastAPI service (`src/career_ops/api.py`)

HTTP front door for everything the engine does. `career-ops serve`
starts it at `:8000` and the OpenAPI schema is at `/docs`.

| Endpoint | What it does |
|---|---|
| `GET /healthz` | liveness + rubric version |
| `GET /stats`   | jobs, evals, grade + visa distribution, pipeline counts |
| `GET /jobs`    | list (paged, filterable by company) |
| `POST /jobs`   | ingest a JD (URL / path / text) |
| `GET /jobs/{id}` | one job |
| `GET /evaluations` | list (filter by grade / company / h1b / min %) |
| `POST /evaluations` | score a job under the current rubric |
| `GET /evaluations/{id}` | one evaluation with per-dim scores + citations |
| `POST /tailor/{eval_id}` | generate a tailored CV PDF |
| `POST /prep/{eval_id}`   | generate STAR-structured interview prep |

Same process can be deployed to a real Python platform (Fly, Render,
ECS) — the SQLite default swaps to Postgres by changing
`CAREER_OPS_DATABASE_URL`.

### 3.11 CLI (`src/career_ops/cli.py`)

`typer`-based:
```
career-ops ingest <url-or-file>
career-ops evaluate <job-id>
career-ops tailor <evaluation-id>
career-ops scan <portal-name>
career-ops batch --portal lever --company <slug>
career-ops dash
career-ops profile reindex
```

### 3.12 Claude Code skills (`.claude/skills/`)

Five skills, each a single `SKILL.md`:
- `evaluate` — "user pastes a JD or URL, you evaluate it, summarize the
  grade and top reasons."
- `tailor-cv` — "given an evaluation id, tailor and open the PDF."
- `scan-portals` — "scrape one or more portals and surface top 10 fits."
- `batch` — "evaluate the queue in parallel."
- `profile` — "help the user edit cv.md / profile.yml / rubric.yml."

Each skill is a thin adapter that (a) decides when to trigger, (b)
constructs the right CLI invocation, (c) formats the CLI's JSON output
for the user.

---

## 4. Data flow: the happy path

```
paste JD URL
   │
   ▼
claude /evaluate            (Claude Code skill)
   │  calls:
   ▼
career-ops ingest <url>     (CLI)
   │  returns job_id=42
   ▼
career-ops evaluate 42
   │  1. embed JD requirements
   │  2. FAISS retrieve over cv.md
   │  3. LLM score with citations
   │  4. ranker → A-/86%
   │  5. persist Evaluation
   ▼
skill summarizes:
  "A-/86 at Anthropic Applied AI Eng. Strong GenAI/RAG fit (cited:
   'Intuit RAG copilot with FAISS'), visa: H-1B sponsor, comp ok,
   location remote-US. Recommend: tailor CV."
   │
   ▼  user: "yes, tailor it"
claude /tailor-cv 7         (Claude Code skill)
   │  calls:
   ▼
career-ops tailor 7 → artifacts/cv_anthropic_2026-04-23.pdf
```

---

## 5. Scoring rubric (v1)

10 dimensions, weighted. Each scored 0–5 by the LLM with citations.

| # | Dimension | Weight | What it measures |
|---|---|---|---|
| 1 | Technical fit — GenAI / RAG / LLM work | 1.5 | Direct overlap with Intuit RAG copilot, prompt eng, Claude/GPT-4 production work |
| 2 | Stack overlap (Python, FastAPI, LangChain, vector DBs, cloud) | 1.2 | JD stack ∩ CV stack |
| 3 | Seniority match | 1.0 | 4+ YoE, IC level (L4–L6 equivalents); penalize L3 and staff-and-up overreach |
| 4 | **Visa sponsorship (H-1B / STEM OPT)** | **1.3** | Soft signal. Sponsor history = high; "no sponsorship" = low but not auto-reject |
| 5 | Domain / industry | 0.8 | Finance, tax, enterprise SaaS, AI-native — areas I've already shipped in |
| 6 | Compensation | 1.0 | Against floor in profile.yml; unknown comp → neutral |
| 7 | Location | 0.8 | Charlotte > remote-US > NYC/Bay hybrid > onsite elsewhere |
| 8 | Growth / trajectory | 1.0 | Role scope, mentorship signals, promotion path |
| 9 | Company stage / stability | 0.8 | Post-PMF startup, well-funded scale-up, or stable big-tech |
| 10 | Culture / mission signals | 0.6 | Engineering culture in JD, Glassdoor deltas, eng-blog presence |

Total weight = 10.0. Rubric is versioned (`rubric_version` on every eval)
so I can re-run old evals after tuning weights.

---

## 6. Non-goals

- **Not a job aggregator.** It does not scrape LinkedIn wholesale. It
  scrapes *portals I point it at* via `targets.yml`.
- **Not an autoapply bot.** It stops at "tailored PDF ready + draft
  message." I click apply.
- **Not a hosted service.** Runs locally. CV and contact info never leave
  my machine except to the LLM provider I choose.

---

## 7. Why Python-first (and not a clone of the Node/Go reference)

| Concern | Node/Go reference | This rebuild |
|---|---|---|
| Embeddings / FAISS | delegated to LLM prompt | first-class, like I'd ship in prod |
| Dashboard | Go (new to me) | Streamlit (on my CV) |
| CV PDF render | puppeteer | WeasyPrint (HTML+CSS, cleaner diffs) |
| Portal scraping | Playwright-node | Playwright-python |
| Persistence | flat files | SQLAlchemy + SQLite (on my CV) |
| Demo value | generic | directly showcases the work at Intuit/BoA |

---

## 8. Roadmap

- **v0 (scaffold, this PR):** repo structure, configs, CV, rubric, skill
  definitions, CLI stubs, one working `evaluate` path end-to-end on a
  pasted JD.
- **v0.5:** Playwright portal scanner for Greenhouse + Lever. Tailor
  pipeline with WeasyPrint.
- **v1:** Streamlit dashboard, batch runner, FastAPI JSON API, Dockerfile,
  GitHub Actions CI.
- **v1.5:** Interview-prep skill (generate behavioral stories from
  experience bullets + STAR structuring), rejection-tracker / A-B test
  resume variants, referral-outreach drafter.
- **v2:** Multi-CV mode (split tracks: ML Eng vs. Data Scientist vs. AI
  Platform), fine-grained LLM-as-judge eval of rubric itself against a
  labeled set of 20 past offers.
