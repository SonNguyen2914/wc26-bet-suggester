# CALIBRATION — V9.2: `mls-2026-v0` (July 24, 2026)

*The methodology is unchanged from `docs/V9/CALIBRATION.md` (the analytic
independent-Poisson ladder with match-cluster bootstrap CIs) — read it for
the full protocol and ablation table. V9.2 refreshes the prod-verified
numbers under the `mls-shadow-v1.2` baseline and records that the approval
gate is a persisted, hash-verifiable, CI-based decision, not a boolean or
a point estimate. The WC26 battery remains in `docs/V7/CALIBRATION.md`.*

## The question
Does a league-fitted model beat what "every team is identical" already
knows — and by how much, with what confidence?

## Headline (prod-verified, live at `GET /api/mls/approval`)
```text
variant             log loss
M0 league + venue   ~1.078
M1 raw ratings      ~1.166   (overfits — worse than baseline)
M2 recency+pooling  ~1.070   (best)

edge                point     95% CI              significant
M2 vs M0 (baseline) +0.0078   [-0.0126, +0.0282]  NO
```
n = 162 (both teams ≥ 5 prior completed games). Analytic scoring (zero MC
noise); 1,000-sample match-cluster bootstrap, fixed seed. (The exact CI
bounds move within a hair as completed fixtures accumulate; the sign and
the not-significant conclusion do not.)

## What it means (the controlling conclusion)
- Raw team ratings **overfit** MLS's noisy samples and lose to a
  league-average baseline.
- Recency + partial pooling **rescues them, decisively** (M2 ≫ M1,
  significant) — the model's headline features earn their place.
- **M2's edge over the naive baseline is within noise (CI spans zero).**
  The model's *construction* is validated; a durable forecasting edge is
  **not established.**

## Why this is the number the gate rests on
`shadow_approval_policy` requires a minimum scored sample and refuses a
model *significantly worse* than baseline — but does NOT require a positive
edge, because shadow means evidence collection. The persisted
`ModelApprovalDecision` (id 4, hash `79c32d7d…`) records this CI, its
content hash **recomputes** from the stored canonical document (audited),
and boot **loads** it rather than recomputing (no drift). The endpoint
states plainly: *safe to collect prospective evidence; NOT an established
executable edge.*

## Caveats (unchanged)
1. **In-sample.** Rolling-origin; hyperparameters chosen on this sample →
   +0.0078 is mildly optimistic; expect "positive, smaller, possibly
   zero." Saturday begins the out-of-sample test. Nested/holdout selection
   and evaluating the deployed stochastic distribution are P1 (not changed
   mid-freeze — they would alter the approval computation).
2. **n=162, one league, one half-season.** No significance claim for
   M2-vs-baseline; the CI is the point.
3. **Forecast quality only.** Market-relative and net-of-fee execution are
   evaluated separately, after settlement — over exact best-10 depth and
   the general-taker fee (series/maker/exit fees not yet modeled, labelled).

## Evidence hierarchy
MEASURED (frozen pre-event, scored on outcome) > REPLAYED (seeded
backtest) > PILOT. Everything here is REPLAYED. **Nothing MLS is MEASURED
yet — that begins with Saturday's slate**, whose locks are engine-matched
(`model-input-v3`) and approval-hash-verified by construction.
