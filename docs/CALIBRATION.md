# Calibration harness

An offline evaluation harness for the rubric itself. Same shape as the
RAG-quality evaluation harnesses I built at Intuit: labeled fixtures,
model-under-test, scalar metrics, and a CI regression gate.

## Why it exists

Rubric weights and LLM prompts drift. Without a regression gate, a
"small prompt tweak" can silently change what counts as an A+, which
makes historical evaluations non-comparable.

The harness pins behavior two ways:

1. **Reference mode** (default, runs in CI) replaces the LLM with a
   deterministic stub that returns each fixture's labeled scores. This
   locks in the ranker + visa-mechanical + grade-threshold pipeline.
2. **Live mode** (opt-in, local) runs the real LLM against each fixture
   and measures agreement with human labels. Used when tuning prompts.

## Fixtures

Under `tests/fixtures/calibration/*.yml`. Each fixture has:

```yaml
id: short_slug
company: "…"
title: "…"
location: "…"
h1b_history: heavy|active|occasional|unknown|none
expected:
  grade: "A+|A|B+|B|C|D|F"
  percent_range: [low, high]           # acceptance band
  dimensions:
    technical_fit_genai: 0-5
    stack_overlap: 0-5
    …                                  # visa_sponsorship omitted — mechanical
jd: |
  <full JD text>
```

## Metrics

| metric | what it measures | CI threshold |
|---|---|---|
| `percent_mae`         | MAE on overall percent score                    | ≤ 2.0 (reference) |
| `grade_agreement`     | fraction of fixtures where predicted == expected grade | ≥ 0.95 (reference) |
| `dim_mae[dim]`        | MAE per rubric dimension                        | ≤ 0.5 (reference) |
| `spearman_per_dim[d]` | rank correlation between expected and predicted | informational (reference) |

For live mode, thresholds are looser (suggested: `grade_agreement ≥
0.75`, `percent_mae ≤ 8`, `dim_mae ≤ 1.0`). Those live-mode checks are
not wired into CI because they'd spend API tokens on every push.

## Running

```bash
# Reference mode — what CI runs.
career-ops calibrate
pytest tests/test_calibration.py -q

# Live mode — opt-in, costs LLM tokens.
career-ops calibrate --mode live

# Machine-readable output (for a CI summary comment).
career-ops calibrate --json
```

## When a test fails

1. Did you change rubric weights or thresholds? If so: bump
   `config/rubric.yml:version`, re-label fixtures (the scores are tied
   to the old rubric), and relax thresholds in
   `tests/test_calibration.py`.
2. Did you change `evaluator._visa_score_from_history`? That one
   function is what the reference-mode run uses for dimension 4;
   reference-mode failure with "visa" in the dim MAE almost always
   points there.
3. Did you change the grade thresholds in `rubric.yml`? Grades shift by
   a letter even if percent MAE is fine — `grade_agreement` catches
   that.

## Growing the fixture set

- Add 1 new fixture per 20 real evaluations you run in production.
- Picking fixtures: cover the grade spectrum (one A+, one B, one D/F)
  and edge cases (no-sponsorship-language, stealth startup, Staff
  overreach, onsite-in-wrong-geo).
- Label the fixture with the grade **you** think is correct, not what
  the model says — otherwise the harness measures agreement with
  itself, not with you.

## Relationship to the rest of the system

```
  config/rubric.yml  ─┐
                      ├──►  ranker.py  ──► grade
  fixture labels   ───┘         ▲
                                │
                        calibration.py (harness)
                                │
                                ▼
                      tests/test_calibration.py  ◄── CI regression gate
```
