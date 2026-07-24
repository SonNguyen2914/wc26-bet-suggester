# Runbook — first MLS shadow slate (mls-shadow-v1.1)

Operational protocol for collecting the first prospective MLS T-10 slate
(Saturday 2026-07-25) under the frozen `mls-shadow-v1.1` baseline. The
remaining risk is **release drift or operational failure during the
slate**, not missing observability. Record evidence at each stage; do not
merely observe informally.

## 0. Freeze (in effect since 2026-07-24T02:08Z)
No change to: model parameters · approval policy · edge thresholds · fee
implementation · execution-readiness rules · market-family policy · lock
timing · simulation count · scheduler behavior · lineup rules · risk
limits. A critical correctness hotfix ⇒ new tag + disclosed protocol
deviation. Begin the hard deploy freeze ≥ 90 min before the first T-10
lock.

## 1. T-90 go/no-go (record each as evidence)

**Scheduler** — record last successful heartbeat, next expected T-10 job,
scheduled fixture count, missed-job count, scheduler instance identity.
*No-go:* heartbeat out of tolerance · missing fixture jobs · duplicate
registrations · next-exec inconsistent with authoritative kickoff ·
restart loop.

**Provider quota & connectivity** — record API-Football quota remaining,
ESPN probe, Kalshi probe, response times, last successful market
discovery. *No-go:* insufficient quota for the full slate · provider
schema mismatch · repeated auth/rate-limit failures · incomplete market
registry · unapproved fixture mappings.

**Notifications** — send one controlled probe through urgent Discord,
detail Discord, ntfy urgent; record send + receipt times. A failure is an
operating incident (⇒ active dashboard monitoring), not automatically an
evidence-invalidation.

**Database backup** — record backup id, creation time, database revision,
release tag, restore-validation status. Prefer a freshly created backup
immediately before the freeze.

> **Tooling note (accept for slate 1).** Only the notifications probe is
> app-triggerable (operator token). Scheduler heartbeat, provider quota,
> and DB backup id are **not** exposed by `/api/ready` or
> `/api/mls/metrics` (P1: durable scheduler heartbeat + provider-health
> endpoint) — capture them manually from the Railway dashboard/logs into
> the evidence record's open checklist.

## 2. Lock-window acceptance (per Saturday fixture)
Every canonical lock must carry:
```text
artifact_schema = model-input-v2
approval_decision_id = 1        approval_decision_hash present
approval model matches run model     approval time precedes run time
stored_engine_signature_hash present   current_engine_signature_hash present
engine_match = true
market snapshot complete        policy version present
lineup snapshot referenced      input artifact referenced
```
A legacy `model-input-v1` run with `engine_match = null` is acceptable as
prior evidence, but **no new v1.1 T-10 lock may look like that.**

## 3. Immediate post-lock checks (per fixture)
- exactly one canonical lock; inside the T-10 window; none created after
  kickoff;
- approval-decision + engine-signature checks all pass;
- input artifact replays with `max_delta = 0`;
- required market families capture-complete; execution readiness evaluated
  *separately*;
- every priced contract references its frozen quote; exact price/quantity
  populated; missing timestamps do not pass as fresh;
- the frontend shows the canonical lock, not a later diagnostic run;
- real-money signals remain disabled.

A book may legitimately be `capture_complete=true` / `execution_ready=
false` — valid evidence; it must simply produce **no paper fill**.

## 4. What invalidates the slate (preserve BOTH fixture- and slate-level status)
- a new lock lacks `model-input-v2`;
- approval decision missing/mismatched;
- engine signature missing/mismatched;
- canonical lock without required market evidence;
- duplicate canonical locks; post-kickoff lock creation;
- quote precision reduced before paper execution;
- missing timestamp accepted as fresh;
- paper fill from a non-execution-ready snapshot;
- a later run replacing the canonical lock in the UI;
- a fixture unexplainedly disappearing from the slate report;
- corpus publication before failures + settlements are finalized.

Some invalidate a single fixture, not the whole slate. The slate report
must never let a fixture vanish because something failed — it lands in
exactly one state (PASS / MISSED / CAPTURE_FAILED / LOCK_FAILED /
EXECUTION_NOT_READY / INTEGRITY_FAILED / SETTLEMENT_FAILED / PENDING /
LEGACY_UNSCORABLE).

## 5. Post-slate sequence (in order)
1. Freeze the database state used for publication.
2. Save the final slate, audit, approval, replay, readiness, metrics
   responses.
3. Confirm every canonical lock references the SAME approval decision
   intended for this release (id 1).
4. Confirm every new lock has `engine_match = true`.
5. Settle forecasts and paper fills idempotently.
6. Record unresolved or corrected results explicitly.
7. **Publish a new immutable corpus version** (`POST /api/admin/mls/
   corpus/publish`, operator-gated) — a never-reused id, e.g.
   `mls-shadow-2026-slate-01` or a dated id.
8. Download the published bytes from `GET /api/mls/corpus?version=…&full=1`.
9. Verify all file + manifest hashes.
10. Run the standalone analyzer with NO production database connection.
11. Replay every eligible run.
12. Reconcile paper fee calculations and fills.
13. Create a post-slate database backup.
14. Write an incident report (including "no incidents" if true).
15. Keep the model unchanged while reviewing the first results.

## 6. What the first slate proves
It is primarily an **infrastructure experiment** — that the frozen
pipeline produces attributable, independently auditable evidence,
preserving failures as carefully as successes. It is NOT yet evidence of
a betting edge; that needs multiple clean prospective slates and
net-of-fee execution evidence. Manual real-money remains not approved.
