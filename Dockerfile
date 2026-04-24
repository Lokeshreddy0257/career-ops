# syntax=docker/dockerfile:1.7
#
# Multi-stage build for career-ops.
# Stage 1 ("builder") compiles wheels; stage 2 ("runtime") is slim.
# The Streamlit dashboard is the default CMD; override for CLI use.

ARG PYTHON_VERSION=3.11

# ── Stage 1: builder ────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY src/ ./src/

RUN pip install --upgrade pip wheel \
 && pip wheel --wheel-dir /wheels -r requirements.txt \
 && pip wheel --wheel-dir /wheels .

# ── Stage 2: runtime ────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/home/app/.local/bin:${PATH}"

# System libs needed by WeasyPrint + Playwright runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libffi-dev \
      fonts-liberation fonts-dejavu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 app

USER app
WORKDIR /home/app/career-ops

COPY --from=builder /wheels /wheels
RUN pip install --user --no-index --find-links=/wheels career-ops \
 && rm -rf /wheels

COPY --chown=app:app . .

# Streamlit config — listen on all interfaces inside the container.
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

# Healthcheck on the Streamlit endpoint.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; \
urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5); \
sys.exit(0)" || exit 1

# Default: run the dashboard. For CLI use, override with e.g.:
#   docker run --rm -v $(pwd):/home/app/career-ops career-ops \
#     career-ops evaluate 1
CMD ["python", "-m", "streamlit", "run", "src/career_ops/dashboard.py"]
