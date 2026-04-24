# Visa notes — F1 OPT / STEM OPT / H-1B

> Personal notes that inform the visa dimension of the rubric. Not legal
> advice. Verify everything with a qualified immigration attorney.

This file exists so the system's visa scoring has an articulated basis,
and so if I ever hand the repo to a reviewer they can see *what* the
soft signal actually means.

## Where I am

- Currently on **F-1 OPT** (12-month post-completion OPT following my MS
  at UNT).
- My MS in Information Systems is a **STEM-designated** degree, which
  means I am eligible for the **24-month STEM OPT extension** (for a
  total of 36 months of OPT post-graduation), provided my employer is
  enrolled in **E-Verify**.
- I do **not** need H-1B sponsorship today.
- I **will** likely need H-1B sponsorship before OPT + STEM extension
  ends.

## Why it's a soft signal, not a hard filter

- Roles at companies with "no sponsorship now or in future" are
  effectively dead ends for me. These end up in `profile.yml:deal_breakers`
  and are filtered out before scoring.
- Everything else — including "unknown", "small startup", "no public
  LCAs" — is better evaluated than rejected. Many sub-100-employee
  companies sponsor when they find the right engineer.
- Therefore the rubric scores visa sponsorship 0–5 based on the
  company's `h1b_history` tag from `config/targets.yml`, with weight
  **1.3** (same order of magnitude as stack overlap).

## How `h1b_history` is assigned

Values and how I set them (roughly):

| tag | rule of thumb |
|---|---|
| `heavy` | ≥10 LCAs filed per year over last 3 years (FAANG, top fintech, top AI labs) |
| `active` | sponsored in last 3 years, lower volume |
| `occasional` | sponsored historically but nothing recent |
| `none` | no LCAs on file at all |
| `unknown` | private / small / stealth — insufficient data |

**Sources** I cross-reference before tagging a company:

- [h1bdata.info](https://h1bdata.info/) — public DOL LCA filings.
- [myvisajobs.com](https://www.myvisajobs.com/) — aggregated LCA data.
- Company careers page / recruiter signals.
- First-hand referrals from people who got sponsored there.

## Interview-stage strategy

1. **Early signal.** Add "authorized to work in the US; require future
   sponsorship" to the application or first recruiter call. Don't bury
   it.
2. **At the offer stage**, get the sponsorship commitment in writing and
   ask about the **H-1B lottery timeline** (whether they'll file the
   following April, whether they've done cap-exempt transfers, whether
   they have a premium-processing policy).
3. **If the lottery is a concern**, ask about:
   - O-1A extraordinary ability pathway (viable with strong publications
     / portfolio / GitHub impact).
   - L-1 if they have an international office and would consider an
     intra-company transfer after I work abroad for a year.
   - EB-2 NIW (National Interest Waiver) — self-petition.

## How this ties into the tailored CV

The tailor pipeline does **not** put visa status in the CV. The CV is for
demonstrating fit; visa status is communicated to the recruiter directly
(where it belongs).
