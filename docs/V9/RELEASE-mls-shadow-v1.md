# Release: mls-shadow-v1 (frozen baseline for the first MLS slate)

> **The actual pre-slate baseline is `mls-shadow-v1.1`, not this tag.**
> Every value in the manifest below is the state *at the original
> `mls-shadow-v1` tag* and is left unchanged as a historical record.
> Since it, the V9 P0-remediation pass + a pre-slate observability patch
> advanced the branch, and the build that collects the first slate is the
> new frozen tag **`mls-shadow-v1.1`** (backend `6aae126`, frontend
> `2eed3ad`, Alembic head `f9a1c0d2b3e4`, input artifacts `model-input-v2`
> with a frozen engine signature, order-level paper fees). The exact,
> hashed pre-slate baseline is recorded in
> [`docs/V9/PRE-SLATE-EVIDENCE-2026-07-25.md`](PRE-SLATE-EVIDENCE-2026-07-25.md);
> the remediation detail is in
> [`docs/V9/P0-REMEDIATIONS.md`](P0-REMEDIATIONS.md). Do NOT treat
> `mls-shadow-v1` as the slate-producing build.

The exact version that collects the first clean MLS T-10 slate
(Saturday July 25, 2026). Frozen per the V8.1 launch-gate roadmap's
step 1 — do not change model behavior during the first collection slate
unless a critical defect appears.

    release:            mls-shadow-v1   (git tag — the source of truth)
    frozen_at:          2026-07-23
    backend_ref:        tag mls-shadow-v1  (= f1abdeb + this manifest)
    frontend_ref:       tag mls-shadow-v1  (2a06214)
    database_revision:  c21ba2ee8df4   (alembic head)
    model_version:      mls-2026-v0    (approved_for_shadow, earned)
    lock_policy:        mls-lock-v1
    audit_policy:       mls-lock-audit-v1
    input_artifact:     model-input-v1
    provider_schema:    kalshi-2026-07-fp

## Acceptance: every lock is attributable
Given any canonical T-10 lock, these identify exactly what produced it:
- prediction_run.git_revision -> backend commit
- prediction_run.model_version_id + model_approved_at_run
- prediction_run.model_input_artifact_id -> the exact input document
  (replayable via GET /api/mls/replay/{run_id}, max_delta 0.0)
- prediction_run.market_snapshot_id -> policy_version + schema_version
- market_snapshot.provider_schema_version

## Launch levels at freeze
1 archive READY · 2 shadow-collection READY · 3 research-reproducibility
READY · 4 exec-paper NOT · 5 manual-money NOT · 6 auto-exec NOT.
Real-money remains disabled; no code path can enable it.

## Verify the deployed release
    GET /api/ready         -> ready true, migrations_current, shadow_ready
    GET /api/mls/audit      -> audit_version mls-lock-audit-v1, content_hash
    GET /api/mls/replay/ID  -> replayable true, max_delta 0.0
