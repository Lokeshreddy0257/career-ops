"""Unit tests for the pure ranker. No LLM, no network."""

from __future__ import annotations

from career_ops.config import Rubric
from career_ops.ranker import compute_percent, grade_for, rank
from career_ops.schemas import DimensionScore


def _rubric() -> Rubric:
    # Minimal synthetic rubric so tests don't depend on config/rubric.yml.
    return Rubric.model_validate({
        "version": "test",
        "scale": [0, 5],
        "grade_thresholds": {"A+": 90, "A": 85, "B+": 78, "B": 70, "C": 60, "D": 50, "F": 0},
        "dimensions": [
            {"id": "a", "name": "A", "weight": 2.0, "description": "", "anchors": {}},
            {"id": "b", "name": "B", "weight": 1.0, "description": "", "anchors": {}},
        ],
    })


def test_compute_percent_all_fives() -> None:
    rubric = _rubric()
    scores = [DimensionScore(dimension_id="a", score=5, reasoning=""),
              DimensionScore(dimension_id="b", score=5, reasoning="")]
    weighted, percent = compute_percent(scores, rubric)
    assert weighted == 3.0  # (1.0 * 2.0) + (1.0 * 1.0)
    assert percent == 100.0


def test_compute_percent_mixed() -> None:
    rubric = _rubric()
    scores = [DimensionScore(dimension_id="a", score=4, reasoning=""),   # 0.8 * 2 = 1.6
              DimensionScore(dimension_id="b", score=3, reasoning="")]    # 0.6 * 1 = 0.6
    weighted, percent = compute_percent(scores, rubric)
    assert round(weighted, 2) == 2.2
    assert round(percent, 2) == round(2.2 / 3.0 * 100, 2)


def test_grade_thresholds() -> None:
    rubric = _rubric()
    assert grade_for(95, rubric) == "A+"
    assert grade_for(87, rubric) == "A"
    assert grade_for(78, rubric) == "B+"
    assert grade_for(70, rubric) == "B"
    assert grade_for(65, rubric) == "C"
    assert grade_for(55, rubric) == "D"
    assert grade_for(10, rubric) == "F"


def test_rank_contract() -> None:
    rubric = _rubric()
    scores = [DimensionScore(dimension_id="a", score=5, reasoning=""),
              DimensionScore(dimension_id="b", score=4, reasoning="")]
    total, percent, grade = rank(scores, rubric)
    assert percent > 90
    assert grade in {"A+", "A"}
