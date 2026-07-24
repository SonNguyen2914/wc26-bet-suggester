# Release: mls-shadow-v1.1 (the slate-producing baseline)

The build that collects the first prospective MLS T-10 slate (Saturday
July 25, 2026). This SUPERSEDES `mls-shadow-v1` as the slate baseline —
that original tag is a historical record only; do not attribute corpus
records to it. Frozen per the launch-gate roadmap: no model behavior
changes during the first collection slate unless a critical defect
appears (which would require a NEW tag + a disclosed protocol deviation).

    release:            mls-shadow-v1.1   (git tag on both repos)
    frozen_at:          2026-07-24T02:08Z
    backend_ref:        tag mls-shadow-v1.1  (commit 4da7d06)
    frontend_ref:       tag mls-shadow-v1.1  (commit 2eed3ad)
    database_revision:  f9a1c0d2b3e4      (alembic head)
    model_version:      mls-2026-v0       (approved_for_shadow, CI-based)
    approval_decision:  id 1 / hash eae6cbbd…594300
    approval_policy:    shadow-approval-v1
    evaluation:         model-eval-v1
    engine_signature:   41c8e08110e204a83ff553eee68ecb9f5d3870b558e07bb913a9a9ec6f9ae75e
    lock_policy:        mls-lock-v1
    audit_policy:       mls-lock-audit-v1
    fee_policy:         kalshi-general-2026-07 / paper-exec-v2 (approximate)
    input_artifact:     model-input-v2 (frozen engine signature)
    provider_schema:    kalshi-2026-07-fp
    real_money_signals: false

## What changed since mls-shadow-v1
- **P0 research-integrity remediations** (12 findings from the third
  independent evaluation): CI-based persisted approval, lineup-failure
  snapshots, provenance foreign keys, cursor-complete discovery, exact
  fixed-point capture, explicit freshness basis, order-level fees,
  immutable published corpus, frozen-engine replay, mode-specific
  readiness, frontend temporal labeling. Detail:
  [`docs/V9/P0-REMEDIATIONS.md`](../V9/P0-REMEDIATIONS.md).
- **Pre-slate observability patch** (evidence-visibility only, no
  behavior change): `GET /api/mls/approval`; the approval-decision
  reference and the engine-signature presence are now *required*
  canonical-lock audit invariants; replay surfaces stored/current engine
  signatures + `engine_match`.

## Acceptance: every canonical lock is attributable
Given any `mls-shadow-v1.1` T-10 lock, these identify exactly what
produced it and are ENFORCED by the audit (`all_pass`):
- `git_revision` → backend commit; `model_version_id` + `model_approved_at_run`
- **`model_approval_decision_id` → the immutable CI-based decision**
  (referenced, exists, model-matches, shadow, precedes run, hash present)
- `model_input_artifact_id` → the exact input document, `model-input-v2`,
  **engine signature present**, replayable via `GET /api/mls/replay/
  {run_id}` with `engine_match=true`, `max_delta=0`
- `market_snapshot_id` → completeness-gated snapshot, policy + schema
  versions, exact fixed-point retained, explicit freshness basis
- `lineup_snapshot_id` → the lineup the lock saw (or an explicit
  `fetch_failed` snapshot)

## Independently verifiable now (no trust required)
`GET /api/mls/approval`, `GET /api/mls/replay/{run_id}`, `GET /api/ready`,
and the self-hashed
[`docs/V9/PRE-SLATE-EVIDENCE-2026-07-25.md`](../V9/PRE-SLATE-EVIDENCE-2026-07-25.md)
(+ `.sha256`) all return the frozen values above.

## Corpus
No immutable corpus is published yet — correct. The first immutable
version is published only *after* the slate's failures and settlements
are finalized (see [`RUNBOOK.md`](RUNBOOK.md) post-slate step). A new,
never-reused id such as `mls-shadow-2026-slate-01` (or a dated id).
