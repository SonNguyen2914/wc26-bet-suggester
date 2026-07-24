# Runbook — first MLS shadow slate (mls-shadow-v1.2)

Operational protocol for the first prospective MLS T-10 slate (Saturday
2026-07-25) under the frozen `mls-shadow-v1.2` baseline. Record evidence
at each stage; do not merely observe informally.

## 0. Freeze
No change to: model · approval policy · edge thresholds · fee arithmetic ·
execution-readiness rules · market-family policy · lock timing · sim count
· scheduler · lineup rules · risk limits. A further critical hotfix ⇒ new
tag + disclosed deviation. Hard deploy freeze ≥ 90 min before the first
T-10 lock.

## 1. Live-book diagnostic (once, BEFORE any lock — do NOT create/reserve a lock)
Prove the production provider schema, parser, and fill engine agree —
already done once at v1.2 and worth repeating at T-90:
1. fetch a raw Kalshi orderbook (`GET {KALSHI}/markets/{ticker}/orderbook`);
2. run it through `_depth_levels` + `yes_buy_ladder`;
3. assert per side: `raw best bid == persisted best bid == fill-engine top`
   and `raw best size == persisted exact size == first fillable size`.
Reference result (v1.2, `KXMLSGAME-26JUL25NEATL-NE`): raw best NO 0.5300 →
YES ask 0.4700, size 37991.07, all three agree; matches prod top-of-book.

## 2. T-90 go/no-go (record each as evidence)
- **Scheduler** — last heartbeat, next T-10 job, scheduled fixture count,
  missed-job count, instance id. *No-go:* heartbeat out of tolerance ·
  missing/duplicate jobs · next-exec inconsistent with kickoff · restart loop.
- **Provider quota/connectivity** — API-Football quota, ESPN/Kalshi probes,
  response times, last discovery. *No-go:* insufficient quota · schema
  mismatch · repeated auth/rate-limit failures · incomplete registry ·
  unapproved mappings.
- **Notifications** — one probe each: urgent Discord, detail Discord, ntfy;
  record send/receipt.
- **Database backup** — backup id, creation time, db revision, release tag,
  restore-validation status; prefer a fresh backup before the freeze.

> Only the notifications probe is app-triggerable. Scheduler heartbeat,
> provider quota, and DB backup id are captured manually from Railway (P1).

## 3. Lock-window acceptance (per fixture)
```text
artifact_schema = model-input-v3
approval_decision_id = 4 (unless explicitly superseded by a new approval event)
approval_decision_hash_valid = true     approval precedes run
engine_signature_present = true         engine_signature_matches_current = true
registry_discovery = complete           market snapshot = complete
freshness_basis = provider (for execution eligibility)
exact depth prices + quantities present  (best_10_each_side)
canonical locks per fixture = exactly one
real_money_signals = false
```
A legacy `model-input-v2` run with `engine_match=false` is prior evidence,
not a v1.2 lock. The approval id must not advance merely on a restart.

## 4. Immediate post-lock checks (per fixture)
- exactly one canonical lock, inside the T-10 window, none after kickoff;
- `all_pass` true (approval hash recomputes, engine matches);
- replay `max_delta = 0`, `engine_match = true`;
- required families capture-complete; execution readiness evaluated
  separately (capture-time-only ⇒ NOT execution-ready ⇒ no paper fill);
- every priced contract references its frozen quote; exact price/qty
  present; frontend shows the canonical lock, not a diagnostic run;
- real-money signals disabled.

## 5. Paper-fill discipline (bounded depth)
`depth_policy = best_10_each_side`. The engine partial-fills when the
retained ladder is exhausted (reason `partial`) — it knows only that
*retained* depth ended, never that no further depth exists. Record
requested / filled / unfilled. Keep proposed quantities well below the
retained ladder's observed capacity for slate 1.

## 6. What invalidates the slate (preserve fixture- AND slate-level status)
new lock lacking `model-input-v3` · approval missing/invalid/mismatched ·
engine signature missing/mismatched · lock without required market
evidence · duplicate canonical locks · post-kickoff lock · quote precision
reduced before paper execution · capture-time book treated as executable ·
paper fill from a non-execution-ready snapshot · later run replacing the
canonical lock in the UI · a fixture vanishing from the slate report ·
corpus publication before failures + settlements are finalized. A fixture
must always land in exactly one state (PASS / MISSED / CAPTURE_FAILED /
LOCK_FAILED / EXECUTION_NOT_READY / INTEGRITY_FAILED / SETTLEMENT_FAILED /
PENDING / LEGACY_UNSCORABLE).

## 7. Post-slate sequence (in order)
1. Freeze the DB state used for publication.
2. Save slate, audit, approval, replay, readiness, metrics responses.
3. Confirm every lock references the same approval decision (id 4).
4. Confirm every v1.2 lock has `engine_match = true`.
5. Settle forecasts + paper fills idempotently.
6. Record unresolved/corrected results explicitly.
7. **Publish a new immutable corpus version** (`POST /api/admin/mls/
   corpus/publish`, never-reused id, e.g. `mls-shadow-2026-slate-01`).
8. Download the published bytes; verify all file + manifest hashes.
9. Run the standalone analyzer with NO production DB connection.
10. Replay every v3 artifact; reconcile every ladder against its quote +
    depth rows; recompute fills independently; confirm exact fees.
11. **Exclude every pre-v1.2 fill** from strategy metrics (there are none
    yet — 0 locks — but enforce the rule).
12. Preserve rejected signals + no-fill outcomes; write an incident report;
    keep the model unchanged after one slate.

Report paper results under: **general-taker, bounded-depth, hold-to-
settlement paper results** — not complete execution returns.

## 8. What the first slate proves
An infrastructure experiment: that the frozen pipeline produces
attributable, independently auditable, execution-faithful evidence,
preserving failures as carefully as successes. NOT yet a betting edge —
that needs multiple clean prospective slates and net-of-fee execution
evidence. Manual real-money remains not approved.
