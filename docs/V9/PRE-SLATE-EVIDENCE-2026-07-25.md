# Pre-Slate Evidence Record — first MLS shadow slate (Saturday 2026-07-25)

The frozen, independently-verifiable baseline that collects the first
prospective MLS T-10 slate. Captured from **production** on
**2026-07-24T02:08:32Z**. This record is itself hashed (see the companion
`.sha256`), and every field below was read from a live prod endpoint or
the tagged source — not asserted from memory.

> **Deployment freeze** begins on capture of this record and holds until
> the slate is collected and settled. Do NOT change the model, thresholds,
> fee formula, lock policy, market-family policy, scheduler, or lineup
> logic during the freeze unless a critical correctness defect appears.

## Build identity

| Field | Value |
|---|---|
| backend build (deployed) | `6aae126` |
| frontend build (deployed) | `2eed3ad` |
| release tag | `mls-shadow-v1.1` (this evidence doc is committed on top of `6aae126`; the tag is the pre-slate baseline) |
| Alembic migration head | `f9a1c0d2b3e4` |

## Model & approval

| Field | Value |
|---|---|
| model version | `mls-2026-v0` |
| approval decision id | `1` |
| approval decision content hash | `eae6cbbd2d30963a7943fbd568aaa559ec37e7c5e2798cae79bf1f6579594300` |
| evaluation version | `model-eval-v1` |
| approval policy version | `shadow-approval-v1` |
| approved mode | `shadow` (no real-money setter exists) |
| approved | `true` |
| approved_at | `2026-07-24T01:17:07Z` |

### The controlling statistical fact (unchanged)

```text
M2 vs baseline (M0): +0.0077 log-loss
95% CI:              [-0.0119, +0.0286]
n_scored:            162
significant:         false  →  NO established advantage
```

Read from `GET /api/mls/approval` — the STORED decision, not a
recomputation. Shadow approval means "safe to collect prospective
evidence," never "edge established."

## Policy versions

| Policy | Version |
|---|---|
| lock policy | `mls-lock-v1` |
| audit policy | `mls-lock-audit-v1` |
| fee policy | `kalshi-general-2026-07` (paper exec `paper-exec-v2`) — **approximate**: no maker/taker, series/event overrides, or exit fees |
| input-artifact schema | `model-input-v2` (engine signature frozen; new runs) |
| provider schema | `kalshi-2026-07-fp` |

## Engine signature (reproducibility)

| Field | Value |
|---|---|
| current engine signature hash | `41c8e08110e204a83ff553eee68ecb9f5d3870b558e07bb913a9a9ec6f9ae75e` |
| source | `GET /api/mls/replay/{run_id}` → `current_engine_signature_hash` |

Every canonical T-10 lock produced during the slate will carry a
`model-input-v2` artifact whose `stored_engine_signature_hash` must match
this value; `verify_replay` refuses on drift. (Runs created before the F4
deploy are `model-input-v1` and have no stored signature — they replay
under the current engine but are not slate locks.)

## Corpus

| Field | Value |
|---|---|
| preview corpus manifest hash | `57fe16af6736e49e16d520ac8198e8c140017645eac9fe92e0056ed26b95ac30` — **preview corpus hash — mutable until publication** |
| published corpus | **NONE published yet** |

No immutable corpus version has been published. The hash above is a live
preview of current state and WILL change as data lands; it is not a
published, frozen artifact. The first immutable corpus is published
*after* the slate is settled (post-slate step 4).

## Readiness (prod, at capture)

```json
{
  "ready": true,
  "readiness": {
    "archive_ready": true,
    "shadow_collection_ready": true,
    "paper_execution_ready": true
  },
  "shadow_blockers": [],
  "real_money_signals": false,
  "live": { "migrations_current": true, "shadow": { "approval_decision_present": true } }
}
```

- `ready: true`; all three mode planes ready, no blockers.
- `real_money_signals: false` — real money remains locked; no code path enables it.
- `approval_decision_present: true` — the CI-based decision exists and is referenced by new runs.
- `migrations_current: true` — no pending migration.

## Known-open (recorded, not blocking)

- `corpus_manifest_hash` on the approval decision is null — approval is
  not yet linked to a *published* corpus (none exists pre-slate).
- Availability is a lineup-confirmation proxy, not a distinct
  injuries/suspensions feed (V9 eval F14 / P1).
- Paper fees are the general schedule only, labelled approximate
  (V9 eval F8/F13 / P1).

## Pre-slate go/no-go checklist

- [x] `ready = true`
- [x] all three readiness planes have no blockers
- [x] `real_money_signals = false`
- [x] CI-based approval decision exists (id 1, hash `eae6cbbd…`)
- [x] fixtures and team aliases complete (30 teams, upcoming fixtures mapped)
- [x] no pending migration (`migrations_current: true`)
- [ ] scheduler heartbeat current — verify at T-90m (not yet a durable metric; P1 F20)
- [ ] API quota sufficient — verify at T-90m
- [ ] notifications working — verify at T-90m
- [ ] production database backup completed — capture backup id at T-90m

Deployment freeze: begin at least 90 minutes before the first relevant
T-10 lock.
