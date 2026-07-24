# Pre-Slate Evidence Record — v1.2 (first MLS shadow slate, 2026-07-25)

The frozen, independently-verifiable baseline that collects the first
prospective MLS T-10 slate, **after the V9.1.2 execution-fidelity hotfix**
(a disclosed deviation over v1.1 — see
[`V1.2-REMEDIATIONS.md`](V1.2-REMEDIATIONS.md)). Captured from **production**
on **2026-07-24T04:1x Z**. Every field was read from a live prod endpoint;
this record is self-hashed (companion `.sha256`).

> **Deployment freeze re-engaged** on capture and holds until the slate is
> collected and settled. No model, threshold, fee, lock-timing, or
> market-family change during the freeze; a further critical hotfix would
> require a new tag + disclosed deviation.

## Build identity

| Field | Value |
|---|---|
| backend build (deployed) | `4e5581e` |
| frontend build (deployed) | `2eed3ad` (unchanged from v1.1) |
| release tag | `mls-shadow-v1.2` (this doc is committed on top of `4e5581e`) |
| Alembic migration head | `a2b3c4d5e6f7` |

## Model & approval (live at `GET /api/mls/approval`)

| Field | Value |
|---|---|
| model version | `mls-2026-v0` |
| approval decision id | `4` |
| approval decision content hash | `79c32d7d1abe6fc91f87f0bf66bffb3c8ad9b798d1b5fcfc914506d13546b4e4` |
| evaluation / approval policy | `model-eval-v1` / `shadow-approval-v1` |
| approved mode | `shadow` |

The audit now **recomputes** this hash from the decision's stored
canonical document and requires it to match (V9.1 eval F4).

### The controlling statistical fact (unchanged)

```text
M2 vs baseline (M0): +0.0078 log-loss
95% CI:              [-0.0126, +0.0282]
n_scored:            162
significant:         false  →  NO established advantage
```

## Engine signature (V9.1 eval F5 — source + runtime)

| Field | Value |
|---|---|
| current engine signature hash | `d18f8bf0f6bf62bb7677c329b45b668aa709afdee5807e2d1e00fcd14b298004` |

Now fingerprints the SOURCE of the model/simulator modules plus runtime,
not just constants+numpy. Every `mls-shadow-v1.2` T-10 lock carries a
`model-input-v3` artifact whose stored signature must equal this and match
under `GET /api/mls/replay/{run_id}` (`engine_match=true`). Pre-v1.2 runs
(`model-input-v2`, signature `41c8e081…`) correctly show `engine_match=
false` — honest, not silently replayed.

## Policy versions

| Policy | Version |
|---|---|
| lock policy | `mls-lock-v1` |
| audit policy | `mls-lock-audit-v1` |
| fee policy | `kalshi-fee-2026-07-general` (paper exec `paper-exec-v3`) — exact centicent Decimal; general taker only, series/maker/exit fees not modeled |
| depth policy | `best_10_each_side` (V9.1 eval F1) |
| input-artifact schema | `model-input-v3` |
| provider schema | `kalshi-2026-07-fp` |

## Corpus

| Field | Value |
|---|---|
| preview corpus manifest hash | `087b8c346a276cf1beb047f631adab0035738e425d926d10e1232ba933a1136f` — **preview corpus hash — mutable until publication** |
| published corpus | **NONE published yet** (published only after the slate settles) |

## Readiness (prod, at capture)

```json
{
  "ready": true,
  "readiness": {
    "archive_ready": true,
    "shadow_collection_ready": true,
    "paper_engine_operational": true,
    "paper_new_entries_allowed": true,
    "paper_kill_switches": []
  },
  "real_money_signals": false,
  "live": { "migrations_current": true }
}
```

- Archive self-healed to 16/16 results, 84/84 ledger, 6/6 lock bundles.
- Paper readiness now splits engine-operational from new-entries-allowed
  and gates on active kill switches (V9.1 eval F7).
- `real_money_signals: false` — real money remains locked.

## Pre-slate go/no-go checklist

- [x] `ready = true`; all planes ready; no blockers
- [x] `real_money_signals = false`
- [x] CI-based approval decision (id 4) exists; hash recomputes (F4)
- [x] engine signature is source+runtime (F5); new locks will match
- [x] depth keeps best-10 per side (F1); paper exact + centicent fees (F2/F3)
- [x] no pending migration (`migrations_current: true`)
- [ ] scheduler heartbeat / API quota / notifications / DB backup id — capture manually at T-90m (not app-exposed; P1)

Deployment freeze: begin ≥ 90 minutes before the first T-10 lock.
