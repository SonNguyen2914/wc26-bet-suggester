# CALIBRATION — V8: `mls-2026-v0` Validation (July 23, 2026)

> **SUPERSEDED BY V9** (`docs/V9/CALIBRATION.md`, Jul 23) — the definitive
> evaluation is now the analytic model-development ladder with bootstrap CIs.

*Successor to `docs/V7/CALIBRATION.md` (the WC26 battery, which remains the
reference for everything tournament-side). This document records how the MLS model
earned — and only just earned — its shadow approval, and what would have to be true
before any stronger claim.*

## The question being answered

Not "is the model good," but the launch decision's narrower gate: **does a
league-fitted model beat what "every team is identical" already knows?** If it
cannot clear a flat baseline, showing its numbers even in shadow would be theater.

## Protocol (rolling-origin walk-forward)

- Universe: all completed MLS 2026 fixtures in the live database (ingested from
  ESPN with frozen final scores), kickoff-ordered.
- For each fixture *i*: fit ratings on fixtures `0..i-1` ONLY (`fit()` is a pure
  function of its inputs — no future leakage by construction), predict through the
  shared Monte Carlo engine, score against the actual result.
- Eligibility: both teams need ≥5 prior completed games → **n = 162 scored
  fixtures** out of 238 completed.
- Baseline: the SAME machinery with all ratings forced to 1.0 — fitted league
  scoring rate and venue split included, so the baseline already knows "MLS-typical
  goals, home advantage exists." The model must add value beyond that.
- Metrics: mean 3-way log loss (primary), Brier, winner hit rate. Deterministic
  seeds throughout; simulation counts stated per table.

## Headline result (4000 simulations, final configuration)

| | log loss | Brier | winner hit |
|---|---|---|---|
| `mls-2026-v0` (k=24, half-life 90d) | **1.0672** | — | 45.1% |
| flat baseline | 1.0746 | — | — |
| **edge (baseline − model)** | **+0.0073** | +0.006 (1k-sim est.) | — |

`beats_baseline: True` → `approved_for_shadow = True` recorded on the
`model_version` row. `approved_for_real_money` remains False and has no setter.

## The part that matters: the first fit LOST

The initial parameterization (k=6 games of shrinkage — a WC26-instinct prior) was
**worse than the flat baseline**: edge −0.0065 at 1000 sims, −0.007 at 4000.
Single-season MLS goal rates are noisy enough that light shrinkage lets noise
masquerade as team quality. The sweep (1000 sims; MC noise ≈ ±0.003 on edge):

| half-life ↓ / k → | 4 | 6 | 10 | 16 | 24 |
|---|---|---|---|---|---|
| 45d | −0.003 | +0.007 | +0.008 | +0.007 | **+0.009** |
| 90d | −0.013 | −0.007 | +0.006 | +0.006 | **+0.009** |
| 180d | −0.025 | −0.013 | −0.001 | +0.002 | +0.005 |
| ~none | −0.025 | −0.019 | −0.005 | +0.006 | +0.007 |

A refinement pass (k up to 56) showed a plateau in k∈[20,56]; a seeming spike at
k=40 (+0.013) **evaporated at 4000 sims** (+0.003) — Monte Carlo noise, treated as
such. Chosen: **k=24, half-life 90d** — near-optimal in every row, stable when
re-simulated, not a single lucky cell.

## Caveats, all of them

1. **Selection effect.** The hyperparameters were chosen on the same walk-forward
   they are scored on. The +0.007 is therefore mildly optimistic; the honest
   expectation for prospective performance is "positive, smaller."
2. **The edge is tiny.** +0.007 log loss over n=162 is far from executable: it
   would not survive Kalshi's spread and fees. This is the standing, in-code
   argument for the money gate.
3. **n=162, one league, one half-season.** No significance claim is made or
   implied. (Compare V7's posture on 293 WC26 markets: parity with the exchange.)
4. **Sim-count sensitivity.** An independent 2000-sim replay at prod boot read
   +0.0036 — same sign, half the size, consistent with the noise band. Quote the
   4000-sim number, expect the range.
5. **Winner hit rate ~45%** on a 3-way is unremarkable and is not the metric; the
   model's value, if any, is in probabilities, not picks.

## What the shadow phase is FOR (the prospective plan)

Starting with the July 25 slate, every fixture gets an atomic T-10 lock: ~35
contract probabilities per fixture (3-way, totals ladder, BTTS, margins,
first-goal, team totals, scorelines) frozen beside the full integer-cent book.
Scoring as results settle (fixture scores already ingest):

- per-family Brier: locked model vs the frozen executable ask — the V7 comparison,
  now prospective and multi-family;
- the 3-way walk-forward re-run on post-launch fixtures only (no selection effect);
- calibration curves once n permits (not before).

Promotion criteria to even DISCUSS `MLS_MANUAL`: a prospective sample of locks
where the model at minimum holds parity with the book after spread — evaluated in
writing, in a V9 edition of this document, not in a config change.

## Evidence hierarchy (carried unchanged from V6/V7)

- **MEASURED** — frozen pre-event numbers scored against outcomes (the T-10 lock
  corpus this document's plan builds).
- **REPLAYED** — seeded backtests over recorded data (everything in this document).
- **PILOT** — small-n live anecdotes (none yet for MLS; the WC26 bot arena's +45%
  remains labeled pilot in perpetuity).

The walk-forward here is REPLAYED. Nothing MLS is MEASURED yet — that begins
Saturday.
