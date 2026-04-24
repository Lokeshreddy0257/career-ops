"""Visa dimension is computed mechanically from targets.yml — not LLM.

These tests pin that contract so a future LLM prompt change can't
silently move sponsorship history into the model's hands.
"""

from __future__ import annotations

from career_ops.evaluator import _visa_score_from_history


def test_heavy_sponsor() -> None:
    d = _visa_score_from_history("heavy")
    assert d.dimension_id == "visa_sponsorship"
    assert d.score == 5


def test_active_sponsor() -> None:
    assert _visa_score_from_history("active").score == 4


def test_unknown_defaults_to_neutral() -> None:
    assert _visa_score_from_history("unknown").score == 3
    assert _visa_score_from_history(None).score == 3


def test_no_sponsorship_still_not_zero() -> None:
    # We specifically never auto-assign 0 to 'none' — that's up to the
    # LLM if the JD explicitly says 'no sponsorship'. Keep the hard-zero
    # for deal_breakers in profile.yml instead of here.
    assert _visa_score_from_history("none").score == 2
