"""Regression gate for the calibration harness.

Reference mode only — no LLM, no network. This pins the deterministic
behavior of the ranker + visa + grade threshold pipeline against the
fixture set in tests/fixtures/calibration/.

If rubric weights change, this test may fail; that's the point —
bumping rubric.version should be paired with re-labeling fixtures and
updating the thresholds below.
"""

from __future__ import annotations

from career_ops.calibration import run_calibration


# These thresholds are deliberately tight for reference mode — it's the
# deterministic path, so we expect near-perfect agreement.
#
# For `live` mode with a real LLM, we'd use looser thresholds (e.g.
# grade_agreement ≥ 0.75, percent_mae ≤ 8) and run it outside CI.

REFERENCE_PERCENT_MAE_MAX = 2.0
REFERENCE_GRADE_AGREEMENT_MIN = 0.95
REFERENCE_DIM_MAE_MAX = 0.5


def test_calibration_reference_mode() -> None:
    report = run_calibration(mode="reference")
    assert report.n >= 3, "expected at least 3 fixtures"
    assert report.percent_mae <= REFERENCE_PERCENT_MAE_MAX, (
        f"percent MAE {report.percent_mae} exceeds {REFERENCE_PERCENT_MAE_MAX}\n"
        f"{report.summary()}"
    )
    assert report.grade_agreement >= REFERENCE_GRADE_AGREEMENT_MIN, (
        f"grade agreement {report.grade_agreement} below "
        f"{REFERENCE_GRADE_AGREEMENT_MIN}\n{report.summary()}"
    )
    for dim, mae in report.dim_mae.items():
        assert mae <= REFERENCE_DIM_MAE_MAX, (
            f"dim {dim} MAE {mae} exceeds {REFERENCE_DIM_MAE_MAX}"
        )


def test_calibration_summary_prints_cleanly() -> None:
    """The summary string is what humans see in CI logs; smoke-test it."""
    report = run_calibration(mode="reference")
    text = report.summary()
    assert "Calibration report" in text
    assert "percent MAE" in text
    assert "grade agreement" in text
