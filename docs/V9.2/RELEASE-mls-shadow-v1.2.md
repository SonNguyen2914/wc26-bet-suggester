# Release: mls-shadow-v1.2 (the slate-producing baseline)

The build that collects the first prospective MLS T-10 slate (Saturday
2026-07-25), after the V9.1.2 execution-fidelity hotfix. Supersedes
`mls-shadow-v1.1` and `mls-shadow-v1` (historical only — do not attribute
corpus records to them). Frozen: no model/threshold/fee/lock-timing/
market-family change during the first slate; a further critical hotfix
requires a new tag + disclosed deviation (as this one was).

    release:            mls-shadow-v1.2   (git tag, both repos)
    frozen_at:          2026-07-24
    backend_ref:        tag mls-shadow-v1.2  (commit f875c6f)
    frontend_ref:       tag mls-shadow-v1.2  (commit 2eed3ad, unchanged)
    database_revision:  a2b3c4d5e6f7      (alembic head)
    model_version:      mls-2026-v0
    approval_decision:  id 4 / hash 79c32d7d1abe6fc91f87f0bf66bffb3c8ad9b798d1b5fcfc914506d13546b4e4
    approval_policy:    shadow-approval-v1
    engine_signature:   d18f8bf0f6bf62bb7677c329b45b668aa709afdee5807e2d1e00fcd14b298004
    lock_policy:        mls-lock-v1
    audit_policy:       mls-lock-audit-v1
    fee_policy:         kalshi-fee-2026-07-general  (exact centicent Decimal)
    exec_policy:        paper-exec-v3
    depth_policy:       best_10_each_side
    input_artifact:     model-input-v3   (source+runtime engine signature)
    provider_schema:    kalshi-2026-07-fp
    real_money_signals: false

## What changed since mls-shadow-v1.1 (the fourth-eval hotfix)
A fourth independent evaluation found a **critical order-book defect**:
the parser kept the WRONG end of Kalshi's ascending bid arrays, dropping
the best levels. Because that corrupts *stored* evidence and zero locks
existed, the freeze was re-opened as a disclosed deviation. Fixes
(detail: [`docs/V9/V1.2-REMEDIATIONS.md`](../V9/V1.2-REMEDIATIONS.md)):
best-N depth (F1), exact Decimal paper execution (F2), exact centicent
fees (F3), audit hash-recompute + engine-match (F4), source+runtime engine
signature (F5), provider-only executable freshness (F6), kill-switch-aware
paper readiness (F7), load-existing approval (F8), structural lock gate
(F9), durable registry-discovery record (F10), corpus overwrite removed
(F12). Proven against a live Kalshi book (see PROJECT_DOC).

## Acceptance: every canonical lock is VALIDATED
The audit's `all_pass` now requires (not merely detects presence):
- `model_approval_decision_id` present; its content hash **recomputes**
  from the stored `decision_document`; approval precedes the run;
- artifact `model-input-v3`, engine signature present and **matching** the
  current engine (`engine_match=true`, `max_delta=0` on replay);
- completeness-gated snapshot, exact fixed-point depth, provider freshness
  for execution eligibility;
- lineup snapshot referenced (or explicit failure); one canonical lock per
  fixture; real-money signals false.

## Independently verifiable now
`GET /api/mls/approval`, `GET /api/mls/replay/{run_id}`, `GET /api/ready`,
and the self-hashed
[`docs/V9/PRE-SLATE-EVIDENCE-v1.2.md`](../V9/PRE-SLATE-EVIDENCE-v1.2.md)
(+`.sha256`) return the frozen values above.

## Corpus
No immutable corpus is published yet — correct. Publish only *after* the
slate's failures and settlements are finalized, under a new never-reused
id (e.g. `mls-shadow-2026-slate-01`).
