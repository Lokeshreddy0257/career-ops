# career-ops

> **AI-powered, RAG-grounded job-search system for ML / GenAI / MLOps roles.**
> Built in Python around Claude Code skills. F1 OPT-aware.

I built this because I was doing the same loop by hand: read a JD, mentally
score it against my resume, rewrite my CV to foreground what matched, track
it in a spreadsheet, repeat. It scales badly and it's not how I'd ship a
production ML system.

So I rebuilt the loop the way I'd build a RAG system at work:

- **cv.md** is the source of truth. It's chunked and embedded into a FAISS
  index with `sentence-transformers`.
- When a job comes in, its requirements are **retrieved against that index**
  so every "you're a fit" claim cites a specific bullet in my CV.
- An LLM scores the job against a **10-dimension weighted rubric** —
  technical fit, stack overlap, seniority, visa sponsorship history, comp,
  location, trajectory, and so on.
- A tailored CV PDF is generated for roles that clear the bar, rewriting
  *only from bullets that already exist in cv.md* (no invented experience).
- A Streamlit dashboard tracks the pipeline.
- A Claude Code skill layer lets me drive the whole thing conversationally.

It's heavily inspired by [santifer/career-ops](https://github.com/santifer/career-ops) —
same idea, deliberately rebuilt in a stack (Python, FastAPI, LangChain,
FAISS, SQLAlchemy, Playwright-python, Streamlit, WeasyPrint) that mirrors
the production work on my resume.

---

## Why another one?

| | santifer/career-ops | this repo |
|---|---|---|
| Evaluator | LLM-as-judge on prompts | RAG over CV + LLM w/ citations |
| Language | Node + Go | Python (FastAPI / LangChain / FAISS) |
| Dashboard | Go | Streamlit |
| Persistence | flat files | SQLAlchemy + SQLite |
| Visa | not modeled | first-class scoring dimension |
| Portfolio value | generic | mirrors my Intuit/BoA stack |

---

## Quickstart

```bash
# 1. Python 3.11, venv, install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

# 2. Personalize (already done for Lokesh — edit as things change)
#    edit cv.md, config/profile.yml, config/targets.yml

# 3. Build the CV index
career-ops profile reindex

# 4. Evaluate a job (paste URL or JD)
career-ops ingest https://boards.greenhouse.io/some-co/jobs/12345
career-ops evaluate 1

# 5. Tailor a CV for a high-grade eval
career-ops tailor 1   # → artifacts/cv_{company}_{date}.pdf

# 6. Prep for the interview (grounded STAR stories from cv.md)
career-ops prep 1     # → artifacts/prep_{company}_{date}.md

# 7. Service + dashboard
career-ops serve      # FastAPI at :8000 (OpenAPI docs at /docs)
career-ops dash       # Streamlit at :8501 (talks to the API if running)

# Or, containerized:
docker compose up     # brings up api + dashboard together
```

### From Claude Code

```
> /evaluate https://boards.greenhouse.io/some-co/jobs/12345
```

Claude reads the skill in `.claude/skills/evaluate/SKILL.md`, invokes
`career-ops ingest` + `evaluate`, and summarizes the grade + reasoning in chat.

---

## Repo layout

```
career-ops/
├── README.md                   ← you are here
├── CLAUDE.md                   ← instructions for Claude Code
├── cv.md                       ← source of truth for my resume
├── pyproject.toml
├── requirements.txt
├── .gitignore
├── config/
│   ├── profile.yml             ← identity, search prefs, visa status
│   ├── rubric.yml              ← 10 scoring dimensions + weights
│   ├── targets.yml             ← target companies + h1b_history tags
│   └── portals.yml             ← Playwright selectors per career-site
├── .claude/
│   └── skills/                  ← symlinked from skills/ at install
│       ├── evaluate/SKILL.md
│       ├── tailor-cv/SKILL.md
│       ├── scan-portals/SKILL.md
│       ├── batch/SKILL.md
│       ├── interview-prep/SKILL.md
│       └── profile/SKILL.md
├── src/
│   └── career_ops/
│       ├── __init__.py
│       ├── cli.py              ← typer entrypoint
│       ├── api.py              ← FastAPI service (evaluate / tailor / prep / stats)
│       ├── ingest.py           ← JD/URL → JobOffer
│       ├── embeddings.py       ← FAISS over cv.md
│       ├── evaluator.py        ← RAG-grounded rubric scoring
│       ├── ranker.py           ← weighted-sum → A/B/C grade
│       ├── calibration.py      ← offline rubric eval harness (ref + live)
│       ├── tailor.py           ← tailored-CV PDF generator
│       ├── prep.py             ← interview-prep STAR story generator
│       ├── portals.py          ← Playwright scanner + JSON-API scanners
│       ├── batch.py            ← async parallel runner
│       ├── storage.py          ← SQLAlchemy models + session
│       ├── schemas.py          ← Pydantic models
│       ├── config.py           ← settings + YAML loaders
│       ├── llm.py              ← model client abstraction
│       └── dashboard.py        ← Streamlit app (talks to api.py)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── SETUP.md
│   ├── RUBRIC.md
│   └── VISA_NOTES.md           ← F1 OPT / STEM / H-1B reasoning
├── templates/
│   └── cv_html.jinja           ← CV → HTML → PDF template
├── data/                        (gitignored — sqlite + faiss live here)
├── artifacts/                   (gitignored — generated PDFs)
└── tests/
    ├── test_ranker.py
    ├── test_evaluator.py
    └── fixtures/
```

---

## Status

**v0: scaffold.** Structure, configs, CV, rubric, Claude Code skills, and a
working single-JD `evaluate` path. Dashboard, portal scanner, tailor
pipeline, and CI are tracked in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#8-roadmap).

---

## License

MIT.
