# CALIBRATION — V9.1: `mls-2026-v0` (July 24, 2026)

*The methodology is unchanged from `docs/V9/CALIBRATION.md` (the analytic
independent-Poisson ladder with match-cluster bootstrap CIs); read it for
the full protocol and the ablation table. What is new in V9.1: the
approval decision is now **persisted and live-verifiable**, and the same
numbers gate the runtime through a confidence-interval policy, not a bare
Monte-Carlo point estimate. The WC26 battery remains in
`docs/V7/CALIBRATION.md`.*

## The question
Does a league-fitted model beat what "every team is identical" already
knows — and by how much, with what confidence?

## Headline (prod-verified, live at `GET /api/mls/approval`)
```text
variant             log loss
M0 league + venue   1.078
M1 raw ratings      1.166   (overfits — worse than baseline)
M2 recency+pooling  1.070   (best)

edge                point     95% CI              significant
M2 vs M0 (baseline) +0.0077   [-0.0119, +0.0286]  NO
M2 vs M1            +0.096    [+0.021, +0.177]     YES
M1 vs M0            -0.088    [-0.186, +0.007]     NO
```
n = 162 (both teams ≥ 5 prior completed games). Analytic scoring (zero MC
noise); 1,000-sample match-cluster bootstrap.

## What it means (the controlling conclusion)
- Raw team ratings **overfit** MLS's noisy small samples and lose to a
  league-average baseline.
- Recency + partial pooling **rescues them, decisively** (M2 ≫ M1,
  significant) — the model's two headline features earn their place.
- **M2's edge over the naive baseline is within noise (CI spans zero).**
  The model's *construction* is validated; a durable forecasting edge is
  **not established.**

## Why this is the number the gate rests on
The V9.1 runtime approves the model for shadow through
`shadow_approval_policy`, which requires a minimum scored sample and
refuses a model that is *significantly worse* than baseline — but does
**not** require a positive edge, because shadow means evidence
collection. The persisted `ModelApprovalDecision` (id 1, hash
`eae6cbbd…594300`) records this CI, and the endpoint states in plain
terms: *safe to collect prospective evidence; NOT an established
executable edge.*

## Caveats (unchanged)
1. **In-sample.** Rolling-origin, hyperparameters chosen on this sample →
   +0.0077 is mildly optimistic; the prospective expectation is
   "positive, smaller, and quite possibly zero." Saturday begins the
   out-of-sample test.
2. **n=162, one league, one half-season.** No significance claim for
   M2-vs-baseline; the CI is the point.
3. **Forecast quality only.** Market-relative and net-of-fee execution
   performance are evaluated separately, after settlement (the paper
   ledger + frozen T-10 books capture what's needed; fees are the
   order-level general schedule, labelled approximate).
4. **The deployed simulator** (gamma-dispersed, red-card) is validated
   analytically at its mean-rate core; validating the full stochastic
   engine prospectively is P1.

## Evidence hierarchy
MEASURED (frozen pre-event, scored on outcome) > REPLAYED (seeded
backtest) > PILOT. Everything here is REPLAYED. **Nothing MLS is MEASURED
yet — that begins with Saturday's slate**, whose locks are engine-matched
and approval-referenced by construction.
