# CLAUDE.md — instructions for Claude Code

This file is read automatically by Claude Code when it opens this repo.
It gives Claude the context and conventions needed to drive the
`career-ops` system on my behalf.

## Who I am

- Name: **Lokesh Reddy**
- Role today: ML Engineer (GenAI); 4+ YoE at Intuit and Bank of America.
- Visa: **F1 OPT (STEM-eligible).** Don't need sponsorship today; will need
  H-1B or equivalent within 2–3 years.
- Location: Charlotte, NC. Open to remote-US and Southeast hybrid.
- Full profile: `config/profile.yml`.
- Resume source of truth: `cv.md`.

## What this repo does

An AI-powered, RAG-grounded job-search system. You (Claude) are the
conversational interface; a Python engine under `src/career_ops/` does the
actual retrieval, scoring, and CV tailoring. See `docs/ARCHITECTURE.md`
for the full picture.

## How to help me

When I ask you to do something job-search-related, pick the right skill:

| I say… | You invoke skill | Which calls… |
|---|---|---|
| "evaluate this JD / URL"                  | `evaluate`         | `career-ops ingest …` + `evaluate` |
| "tailor my CV for that role"              | `tailor-cv`        | `career-ops tailor <eval-id>` |
| "scan Greenhouse at Anthropic"            | `scan-portals`     | `career-ops scan anthropic` |
| "run through the queue / batch evaluate"  | `batch`            | `career-ops batch …` |
| "update my CV / profile / rubric"         | `profile`          | editor + `career-ops profile reindex` |
| "prep for that interview / onsite"        | `interview-prep`   | `career-ops prep <eval-id>` |

## Conventions

1. **Never invent experience.** When tailoring a CV, every bullet you
   produce must be traceable to an existing bullet in `cv.md`. Reorder
   and reword; do not fabricate.
2. **Always cite.** Evaluations must cite specific `cv.md` chunks for
   each rubric dimension. No "strong fit because GenAI experience" —
   instead "strong fit, cites: *Intuit — Designed a domain-tuned RAG
   copilot using FAISS and Sentence Transformers…*".
3. **Visa is soft, not a hard filter.** If a role explicitly states
   "no sponsorship now or in future," mention it prominently and
   downweight dimension 4. Otherwise, score and continue.
4. **Be blunt.** If a role is a C or below, say so in one sentence and
   move on. Don't talk me into applying to bad fits.
5. **Store, don't re-ask.** After ingesting a JD, everything lives in
   `data/career.db`. Refer to records by id; don't re-fetch.

## Running commands

The Python engine is installed as `career-ops`. Invoke it from repo root.
Typical flow:

```
career-ops ingest <url-or-path>
career-ops evaluate <job-id>
career-ops tailor <evaluation-id>
career-ops prep <evaluation-id>
career-ops scan <company-slug-from-targets.yml>
career-ops batch --ids 1,2,3  |  --unevaluated --limit 10
career-ops calibrate --mode reference|live
career-ops serve                  # FastAPI at :8000 (/docs for OpenAPI)
career-ops dash                   # Streamlit at :8501
career-ops profile reindex
career-ops profile validate
```

When a command produces JSON, format it for me as a short summary. When it
writes a PDF to `artifacts/`, give me the path.

## Editing rules

- `cv.md`, `config/profile.yml`, `config/targets.yml`, `config/rubric.yml`
  are **user-owned**. Do not auto-edit them; if you think they should
  change, draft a diff and ask.
- Anything under `src/`, `docs/`, `templates/`, `.claude/skills/` you
  may edit freely when asked.
- Never commit `data/`, `artifacts/`, `.env`, or anything with personal info.

## When you don't know

If a command fails, a portal selector is stale, or a JD seems
ambiguous — **ask me.** Don't guess. I'd rather answer one question
than review a bad evaluation.
