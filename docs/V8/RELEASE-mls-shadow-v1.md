# Release: mls-shadow-v1 (frozen baseline for the first MLS slate)

The exact version that collects the first clean MLS T-10 slate
(Saturday July 25, 2026). Frozen per the V8.1 launch-gate roadmap's
step 1 — do not change model behavior during the first collection slate
unless a critical defect appears.

    release:            mls-shadow-v1
    frozen_at:          2026-07-23
    backend_commit:     f1abdebec6be00316767746c1a199030cf4ac72a
    frontend_commit:    2a0621428dde84d9f2139e8ced17b7fb4ada11dd
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
