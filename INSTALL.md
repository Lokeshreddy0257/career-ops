# Install — quick-start

This is the copy-paste version. For the full walkthrough with
troubleshooting see [docs/SETUP.md](docs/SETUP.md).

## Option A — one-shot script

From the repo root:

```bash
bash install.sh
```

It checks Python, creates `.venv`, installs everything, installs
Playwright's chromium, symlinks `skills/` into `.claude/skills/`,
seeds `.env`, and validates the configs. Safe to re-run.

## Option B — manual

```bash
# 0. You should be cd'd into the repo. Replace PATH_TO_REPO.
cd ~/code/career-ops

# 1. venv
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel

# 2. install the project + dev deps
pip install -e ".[dev]"

# 3. Playwright (for portal scanning)
python -m playwright install chromium

# 4. link Claude Code skills
mkdir -p .claude
ln -s "$(pwd)/skills" .claude/skills

# 5. create .env and paste your Anthropic API key
cp .env.example .env
#   open .env, set:
#     CAREER_OPS_LLM_PROVIDER=anthropic
#     CAREER_OPS_ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx

# 6. sanity-check configs
career-ops profile validate

# 7. build the RAG index over cv.md (first run downloads a ~90MB model)
career-ops profile reindex
```

## macOS system prerequisites

If you want tailored PDFs (WeasyPrint) to work, install the C libs:

```bash
brew install pango cairo libffi
```

You can skip this until you first run `career-ops tailor`.

## First evaluation (smoke test)

```bash
# Paste any Greenhouse / Lever / Ashby URL
career-ops ingest "https://boards.greenhouse.io/anthropic/jobs/REPLACE_ME"
career-ops evaluate 1
```

Expected output: `**A / 87%** — <title> @ Anthropic (…)` with 10
per-dimension scores and citations.

## Run the API + dashboard

```bash
# terminal 1
career-ops serve
# → http://localhost:8000/docs  (OpenAPI)

# terminal 2
career-ops dash
# → http://localhost:8501
```

## Or use Docker

```bash
docker compose up --build
# API        → http://localhost:8000
# dashboard  → http://localhost:8501
```

## Uninstall

```bash
deactivate                 # if venv is active
rm -rf ~/code/career-ops/.venv
rm -rf ~/code/career-ops/data     # deletes SQLite + FAISS index
rm -rf ~/code/career-ops/artifacts
```
