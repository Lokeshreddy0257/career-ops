.PHONY: help install dev-install playwright lint fmt type test test-fast test-calibration reindex dash clean docker-build docker-up

PY ?= python3
PIP ?= $(PY) -m pip

help:   ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## install runtime deps
	$(PIP) install -e .

dev-install: ## install runtime + dev deps + playwright
	$(PIP) install -e ".[dev]"
	$(PY) -m playwright install chromium

playwright: ## install playwright browsers only
	$(PY) -m playwright install chromium

lint: ## ruff check
	$(PY) -m ruff check src tests

fmt: ## ruff format
	$(PY) -m ruff format src tests

type: ## mypy type check
	$(PY) -m mypy src

test: ## full test suite (needs 3rd-party deps installed)
	$(PY) -m pytest -q

test-fast: ## ranker + visa tests only — no LLM, no faiss
	$(PY) -m pytest -q tests/test_ranker.py tests/test_visa_scoring.py

test-calibration: ## run the offline rubric calibration harness (no live LLM)
	$(PY) -m pytest -q tests/test_calibration.py

reindex: ## rebuild FAISS index over cv.md
	career-ops profile reindex

dash: ## open the Streamlit dashboard
	career-ops dash

docker-build: ## build docker image
	docker build -t career-ops:latest .

docker-up: ## run dashboard via docker-compose
	docker compose up --build

clean: ## remove caches + generated artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache __pycache__ src/career_ops/__pycache__ tests/__pycache__ .coverage htmlcov dist build *.egg-info
