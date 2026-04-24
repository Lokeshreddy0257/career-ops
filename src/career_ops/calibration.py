"""Offline calibration harness for the rubric.

Same shape as the offline-eval harnesses I built at Intuit for RAG
quality: a fixture set with ground-truth labels, a runner that produces
predicted labels, and a small bundle of metrics + a regression gate.

Two modes:

  mode="reference" (default, used in CI / unit tests)
    The "LLM" is replaced by a deterministic stub that returns the
    fixture's `expected.dimensions` verbatim. This lets us regression-
    test the *deterministic* parts of the pipeline (visa mechanical
    score, ranker weighting, grade thresholds) without network access
    or LLM tokens.

  mode="live"
    Calls the real LLM via `evaluator._call_llm_equivalent`. Useful
    for humans tuning prompts locally; never runs in CI.

Metrics reported:
  - percent_mae       : mean absolute error on overall percent
  - grade_agreement   : fraction of fixtures where predicted grade == expected
  - spearman_per_dim  : Spearman rank correlation for each dimension
  - dim_mae           : mean absolute error per dimension
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

import yaml
from pydantic import BaseModel, Field

from .config import REPO_ROOT, Rubric
from .evaluator import _visa_score_from_history
from .ranker import rank
from .schemas import DimensionScore


Mode = Literal["reference", "live"]


# ── Fixture schema ───────────────────────────────────────────────────

class ExpectedScores(BaseModel):
    grade: str
    percent_range: list[float] = Field(min_length=2, max_length=2)
    dimensions: dict[str, int]           # dim_id → 0-5


class CalibrationFixture(BaseModel):
    id: str
    company: str
    title: str
    location: str | None = None
    h1b_history: str = "unknown"
    expected: ExpectedScores
    jd: str

    @classmethod
    def load_dir(cls, path: Path) -> list["CalibrationFixture"]:
        items: list[CalibrationFixture] = []
        for f in sorted(path.glob("*.yml")):
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            items.append(cls.model_validate(data))
        return items


# ── Report types ─────────────────────────────────────────────────────

@dataclass
class FixtureResult:
    fixture_id: str
    expected_grade: str
    predicted_grade: str
    expected_percent: float           # midpoint of expected_range
    predicted_percent: float
    dim_errors: dict[str, float] = field(default_factory=dict)


@dataclass
class CalibrationReport:
    mode: Mode
    rubric_version: str
    n: int
    percent_mae: float
    grade_agreement: float
    dim_mae: dict[str, float]
    spearman_per_dim: dict[str, float]
    results: list[FixtureResult]

    def summary(self) -> str:
        lines = [
            f"Calibration report  (mode={self.mode}, rubric={self.rubric_version}, n={self.n})",
            f"  overall percent MAE : {self.percent_mae:.2f}",
            f"  grade agreement     : {self.grade_agreement * 100:.0f}%",
            "  per-dimension MAE:",
        ]
        for dim, mae in sorted(self.dim_mae.items()):
            corr = self.spearman_per_dim.get(dim, float("nan"))
            lines.append(f"    {dim:<25} MAE={mae:.2f}  ρ={corr:.2f}")
        return "\n".join(lines)


# ── Runner ───────────────────────────────────────────────────────────

DEFAULT_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "calibration"


def run_calibration(
    fixture_dir: Path | None = None,
    mode: Mode = "reference",
) -> CalibrationReport:
    rubric = Rubric.current()
    fixtures = CalibrationFixture.load_dir(fixture_dir or DEFAULT_FIXTURE_DIR)
    if not fixtures:
        raise RuntimeError(f"No fixtures in {fixture_dir or DEFAULT_FIXTURE_DIR}")

    results: list[FixtureResult] = []
    per_dim_expected: dict[str, list[int]] = {}
    per_dim_predicted: dict[str, list[int]] = {}

    for fx in fixtures:
        predicted_scores = _predict_scores(fx, rubric, mode=mode)

        # rank
        _, percent, grade = rank(predicted_scores, rubric)
        expected_percent = sum(fx.expected.percent_range) / 2.0

        dim_errors: dict[str, float] = {}
        for dim_id, exp_score in fx.expected.dimensions.items():
            pred = next(
                (s.score for s in predicted_scores if s.dimension_id == dim_id),
                None,
            )
            if pred is None:
                continue
            dim_errors[dim_id] = abs(pred - exp_score)
            per_dim_expected.setdefault(dim_id, []).append(exp_score)
            per_dim_predicted.setdefault(dim_id, []).append(pred)

        results.append(
            FixtureResult(
                fixture_id=fx.id,
                expected_grade=fx.expected.grade,
                predicted_grade=grade,
                expected_percent=round(expected_percent, 2),
                predicted_percent=round(percent, 2),
                dim_errors=dim_errors,
            )
        )

    # Aggregate metrics
    n = len(results)
    percent_mae = sum(abs(r.expected_percent - r.predicted_percent) for r in results) / n
    grade_agreement = sum(1 for r in results if r.expected_grade == r.predicted_grade) / n

    dim_mae: dict[str, float] = {}
    spearman: dict[str, float] = {}
    for dim_id in per_dim_expected:
        errors = [
            abs(e - p)
            for e, p in zip(per_dim_expected[dim_id], per_dim_predicted[dim_id])
        ]
        dim_mae[dim_id] = sum(errors) / len(errors) if errors else 0.0
        spearman[dim_id] = _spearman(
            per_dim_expected[dim_id], per_dim_predicted[dim_id]
        )

    return CalibrationReport(
        mode=mode,
        rubric_version=rubric.version,
        n=n,
        percent_mae=round(percent_mae, 3),
        grade_agreement=round(grade_agreement, 3),
        dim_mae={k: round(v, 3) for k, v in dim_mae.items()},
        spearman_per_dim={k: round(v, 3) for k, v in spearman.items()},
        results=results,
    )


# ── Mode implementations ─────────────────────────────────────────────

def _predict_scores(
    fx: CalibrationFixture, rubric: Rubric, *, mode: Mode
) -> list[DimensionScore]:
    """Produce per-dimension predicted scores for one fixture."""
    if mode == "reference":
        # Deterministic: use fixture-provided expected scores for
        # LLM-judged dimensions and recompute visa mechanically.
        scores: list[DimensionScore] = []
        for dim in rubric.dimensions:
            if dim.id == "visa_sponsorship":
                scores.append(_visa_score_from_history(fx.h1b_history))
                continue
            exp = fx.expected.dimensions.get(dim.id)
            if exp is None:
                # missing label → neutral so we don't bias MAE
                exp = 3
            scores.append(
                DimensionScore(
                    dimension_id=dim.id,
                    score=exp,
                    reasoning="[reference mode: echoed fixture label]",
                    citations=[],
                )
            )
        return scores

    if mode == "live":
        # Run the actual evaluator. This path is opt-in and not used in
        # CI. It expects network access, API keys, and a built FAISS
        # index. We import lazily so `reference` mode never touches it.
        from . import ingest, evaluator, storage

        storage.init_db()
        jd_path = REPO_ROOT / f".cache/calibration_{fx.id}.txt"
        jd_path.parent.mkdir(parents=True, exist_ok=True)
        jd_path.write_text(fx.jd, encoding="utf-8")
        job_id = ingest.ingest(str(jd_path), source_kind="text")
        ev = evaluator.evaluate_job(job_id)
        return ev.dimension_scores

    raise ValueError(f"Unknown calibration mode: {mode!r}")


# ── Spearman (inline so we don't pull scipy) ─────────────────────────

def _spearman(x: list[int], y: list[int]) -> float:
    """Spearman rank correlation coefficient. Returns NaN if undefined."""
    if len(x) != len(y) or len(x) < 2:
        return float("nan")
    xr = _rank(x)
    yr = _rank(y)
    n = len(x)
    mean_xr = sum(xr) / n
    mean_yr = sum(yr) / n
    num = sum((xr[i] - mean_xr) * (yr[i] - mean_yr) for i in range(n))
    den_x = math.sqrt(sum((xr[i] - mean_xr) ** 2 for i in range(n)))
    den_y = math.sqrt(sum((yr[i] - mean_yr) ** 2 for i in range(n)))
    if den_x == 0 or den_y == 0:
        return float("nan")
    return num / (den_x * den_y)


def _rank(values: Iterable[int]) -> list[float]:
    values = list(values)
    sorted_pairs = sorted(enumerate(values), key=lambda p: p[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_pairs):
        j = i
        while (
            j + 1 < len(sorted_pairs)
            and sorted_pairs[j + 1][1] == sorted_pairs[i][1]
        ):
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[sorted_pairs[k][0]] = avg_rank
        i = j + 1
    return ranks
