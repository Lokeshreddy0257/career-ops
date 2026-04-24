# Rubric — design notes

The source of truth is `config/rubric.yml`. This file explains **why**
each dimension exists, why the weights are what they are, and how to
evolve it.

## Design principles

1. **Calibrated to my actual leverage.** Technical fit on GenAI / RAG is
   the heaviest dimension (weight 1.5) because it's where my Intuit
   work differentiates me and where the premium hiring is happening.
2. **Visa is weighted, not gated.** Soft signal at 1.3 — see
   [VISA_NOTES.md](VISA_NOTES.md).
3. **Location is weighted low (0.8)** because remote-US has become the
   norm for the roles I target.
4. **Culture is weighted lowest (0.6)** because it's the noisiest signal
   (Glassdoor bias, self-selection) — I don't want it to swing grades.
5. **Seniority is neutral-weighted (1.0)** — overrunning with "Staff"
   roles is actually fine, under-leveling is not.

## Why 10 dimensions, not 5 or 20

- <10: dimensions collapse and reasoning becomes vague ("technical
  fit" ends up doing too much work).
- >15: the LLM's output quality drops and weights become noise.
- 10 is the sweet spot — enough to disentangle fit / stack / seniority /
  visa / domain / comp / location / growth / stage / culture.

## Versioning

Every `EvaluationRow` persists the `rubric_version` at scoring time. If
I tune weights:

1. Bump `version` in `config/rubric.yml` (e.g. `2026.04.23` → `2026.06.01`).
2. Run `career-ops profile validate` to confirm parse + Σweight.
3. Re-run evaluations I care about with `career-ops evaluate <id>`; the
   old row stays for reference.

## Calibration plan (roadmap)

- Pick 20 past offers (real or made-up from target JDs).
- Score each manually on the 10 dimensions.
- Run the same through `evaluator.py`.
- Compare per-dimension Spearman + overall grade agreement.
- Tune weights / prompts until agreement is acceptable.

This is the eval-harness work I'd do for any production LLM scoring
system — same shape as the offline evals I ran at Intuit for RAG
quality.
