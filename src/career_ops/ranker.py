"""Pure ranking logic. Takes an Evaluation's dimension scores and produces
a weighted percent + letter grade.

This is deliberately not LLM-driven — it's the auditable, deterministic
layer that makes evaluations reproducible.
"""

from __future__ import annotations

from .config import Rubric
from .schemas import DimensionScore


def compute_percent(scores: list[DimensionScore], rubric: Rubric) -> tuple[float, float]:
    """Return (weighted_total, percent).

    weighted_total = Σ (score / max_scale) * weight
    percent        = weighted_total / Σ weight * 100
    """
    max_scale = rubric.scale[1]
    weighted_total = 0.0
    total_weight = 0.0
    for s in scores:
        dim = rubric.dimension(s.dimension_id)
        weighted_total += (s.score / max_scale) * dim.weight
        total_weight += dim.weight
    if total_weight == 0:
        return 0.0, 0.0
    percent = (weighted_total / total_weight) * 100
    return weighted_total, round(percent, 2)


def grade_for(percent: float, rubric: Rubric) -> str:
    """Map a percent to a letter grade using rubric.grade_thresholds."""
    # sort thresholds descending so A+ is checked before A, etc.
    sorted_thresholds = sorted(
        rubric.grade_thresholds.items(), key=lambda kv: kv[1], reverse=True
    )
    for letter, threshold in sorted_thresholds:
        if percent >= threshold:
            return letter
    return "F"


def rank(scores: list[DimensionScore], rubric: Rubric) -> tuple[float, float, str]:
    """Convenience wrapper returning (weighted_total, percent, grade)."""
    weighted_total, percent = compute_percent(scores, rubric)
    return weighted_total, percent, grade_for(percent, rubric)
