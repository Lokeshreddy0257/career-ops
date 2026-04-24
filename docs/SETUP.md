# Setup

End-to-end setup for a fresh machine. Steps are written for macOS; the
Linux flow is identical modulo the first step.

---

## 0. Prerequisites

- **Python 3.11+** (`python --version`).
- **Claude Code** — you already have it.
- (Optional, for portal scanning and PDF rendering) system libs:
  - macOS: `brew install pango libffi cairo`
  - Ubuntu: `sudo apt install -y libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libffi-dev`

---

## 1. Clone and install

```bash
cd ~/code                             # or wherever you keep repos
git init career-ops && cd career-ops  # or clone if you've already pushed
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
python -m playwright install chromium
```

This installs the CLI as `career-ops`. Verify:

```bash
career-ops --help
```

---

## 2. Configure secrets

Create `.env` at the repo root:

```env
# Pick one provider.
CAREER_OPS_LLM_PROVIDER=anthropic
CAREER_OPS_ANTHROPIC_API_KEY=sk-ant-...

# OR for OpenAI:
# CAREER_OPS_LLM_PROVIDER=openai
# CAREER_OPS_OPENAI_API_KEY=sk-...
```

`.env` is gitignored.

---

## 3. Link the Claude Code skills

Skills are version-controlled under `skills/` and Claude Code reads
`.claude/skills/`. Link them once:

```bash
mkdir -p .claude
ln -s "$(pwd)/skills" .claude/skills
```

If you prefer copies (e.g. on Windows):

```bash
mkdir -p .claude/skills
cp -R skills/* .claude/skills/
```

---

## 4. Personalize (already done for you, edit as things change)

- `cv.md`                  — resume source of truth
- `config/profile.yml`     — identity, roles, comp, visa
- `config/targets.yml`     — companies you're interested in
- `config/rubric.yml`      — scoring weights

Run the validator to confirm your YAML is clean:

```bash
career-ops profile validate
```

---

## 5. Build the RAG index over your CV

```bash
career-ops profile reindex
```

This chunks `cv.md`, embeds with `sentence-transformers/all-MiniLM-L6-v2`,
and writes `data/cv.faiss` plus a metadata sidecar.

---

## 6. First evaluation

Grab any JD URL (Greenhouse, Lever, Ashby) and:

```bash
career-ops ingest https://boards.greenhouse.io/anthropic/jobs/4000000
# → prints: Ingested → job_id=1

career-ops evaluate 1
# → prints: A / 87% — Senior ML Engineer @ Anthropic (Remote-US)
#           • technical_fit_genai: 5/5 — "Designed a domain-tuned RAG copilot..."
#           • stack_overlap: 4/5 — Python + LangChain + FAISS
#           • visa_sponsorship: 5/5 — tag 'heavy'
#           ...
```

---

## 7. Tailored CV

```bash
career-ops tailor 1
# → artifacts/cv_anthropic_YYYY-MM-DD.pdf
```

Open the PDF. Every bullet is traceable back to a bullet in cv.md.

---

## 8. API + Dashboard

Option A — run both manually, in two terminals:

```bash
# terminal 1
career-ops serve            # FastAPI on http://localhost:8000
#                             OpenAPI docs at http://localhost:8000/docs

# terminal 2
career-ops dash             # Streamlit on http://localhost:8501
#                             talks to the API if CAREER_OPS_API_URL is set,
#                             otherwise falls back to direct DB access.
```

Option B — one-shot via Docker:

```bash
docker compose up --build
# API        → http://localhost:8000
# Dashboard  → http://localhost:8501
```

The API and dashboard share the same SQLite DB via a bind-mount, so
evaluations persist across restarts.

---

## 9. Claude Code flow

With `.claude/skills/` linked, open the repo in Claude Code and try:

```
/evaluate https://jobs.lever.co/cohere/some-posting
```

The `evaluate` skill triggers, runs the CLI, and summarizes the result
in chat. Ask follow-ups like "tailor for that" and Claude will invoke
`tailor-cv` next.

---

## 10. Testing + lint

```bash
pytest -q
ruff check src tests
```

`tests/test_ranker.py` and `tests/test_visa_scoring.py` run without
network / LLM access — they pin the deterministic parts of the system.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: faiss` | `pip install faiss-cpu` (mac ARM needs Python 3.11 wheel) |
| WeasyPrint can't find pango | install the system libs in §0 |
| Playwright: `Executable doesn't exist` | `python -m playwright install chromium` |
| `KeyError: 'visa_sponsorship'` in ranker | rubric was edited but version not bumped; run `profile validate` |
| LLM returns malformed JSON | bump `temperature` to 0.0 in `.env`; or inspect `llm.py:_extract_json` |
