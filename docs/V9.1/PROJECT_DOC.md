# WC26 → Multi-League Platform — Project Documentation (V9.1)

**V9.1 — July 24, 2026. THE FROZEN PRE-SLATE EDITION.** V9 was the
validation-ready edition; V9.1 records what a *third* independent
evaluation prescribed and the project shipped in response — the P0
research-integrity remediations, a pre-slate observability patch, and a
new frozen release baseline — then locks the system for its first
prospective matchday. Every P0 that is *code* is deployed and
prod-verified; the runtime now exposes enough for an external reviewer to
audit each canonical lock independently; and every path to real money
stays closed. Docs live in `docs/V9.1/`; V9 is superseded-but-kept as the
pre-remediation snapshot; V8 the expansion snapshot; V7/V6 the
tournament-hardening and tournament-close editions; V5 on
`docs-v5-handoff`.

---

## ⚡ CURRENT STATE — V9.1 SNAPSHOT

### The one-paragraph version
One backend, two planes, strictly isolated. The **archive plane** is the
completed WC26 research record: fail-closed read-only, self-healing at
every deploy (16 results / 84 ledger / 6 canonical lock bundles). The
**live plane** is the MLS shadow platform on durable Railway PostgreSQL:
the full 2026 season ingested from ESPN, approved-alias identity, capture
across all 12 per-match Kalshi families (exact fixed-point retained), and
`mls-2026-v0` — a league-fitted model whose every run is a status-gated
evidence package. Each canonical **T-10 lock** must now reference (as
*enforced audit invariants*, not optional metadata): a persisted,
CI-based **approval decision**; a versioned **model-input artifact** with
a frozen **engine signature**; a completeness-gated **market snapshot**
with the **lineup it saw** (recorded even on a provider fetch failure);
and a frozen **quote** per priced contract. On top: depth-aware paper
trading (order-level general fees, approximate), a central risk engine, a
model-eval ladder with bootstrap CIs, an immutable published-corpus
mechanism, mode-specific readiness, observability, and a slate scorecard.
**Real-money recommendations are disabled and no code path can enable
them.**

### The frozen baseline
```text
release:            mls-shadow-v1.1
backend:            4da7d06   (deployed build 6aae126 + pre-slate evidence)
frontend:           2eed3ad
alembic head:       f9a1c0d2b3e4
model version:      mls-2026-v0
approval decision:  id 1 / hash eae6cbbd…594300
engine signature:   41c8e081…ae75e
real-money signals: false
```
The exact, self-hashed record is
[`docs/V9/PRE-SLATE-EVIDENCE-2026-07-25.md`](../V9/PRE-SLATE-EVIDENCE-2026-07-25.md);
the release manifest is
[`RELEASE-mls-shadow-v1.1.md`](RELEASE-mls-shadow-v1.1.md); the
finding-by-finding remediation response is
[`docs/V9/P0-REMEDIATIONS.md`](../V9/P0-REMEDIATIONS.md).

### The controlling statistical fact (unchanged, now live-verifiable)
```text
M2 vs baseline: +0.0077 log-loss   95% CI [-0.0119, +0.0286]   n=162   NOT significant
```
Read live from `GET /api/mls/approval`. Shadow approval means *safe to
collect prospective evidence*, never *edge established*.

---

## The evidence / integrity contract (what V9.1 adds)

For every new `mls-shadow-v1.1` canonical lock, three questions are
answerable independently of any documentation claim:

1. **Authorized under the declared statistical policy?** The run
   references a persisted `ModelApprovalDecision` (content hash, CI,
   timestamp, policy version) — surfaced at `GET /api/mls/approval`, and a
   *required* audit check (`approval_decision_referenced` + exists +
   model-matches + shadow + precedes-run + hash-present).
2. **Replayable under the correct engine?** The input artifact
   (`model-input-v2`) freezes an engine signature; `GET /api/mls/replay/
   {run_id}` exposes stored vs current signatures and `engine_match`, and
   refuses on drift. `engine_signature_present` is a required audit check.
3. **Pass/fail independent of docs?** The lock audit ENFORCES the
   approval + engine evidence (all_pass), not merely reports it.

## Endpoint surface (public, read-only; mutations fail-closed)

```
/api/ready                     mode-specific: archive / shadow_collection / paper_execution
/api/mls/scoreboard|schedule|standings|markets
/api/mls/match/{eventId}       match hub: model (primary=T-10 lock) + current book + temporal basis
/api/mls/odds                  shadow odds board
/api/mls/model-eval            M0/M1/M2 ladder + bootstrap CIs + approval_record
/api/mls/approval              the STORED approval decision (no recompute)   ← V9.1
/api/mls/replay/{run_id}       replay + stored/current engine signature + engine_match  ← V9.1 fields
/api/mls/audit                 lock integrity audit (approval + engine now required)    ← V9.1 checks
/api/mls/corpus                published (immutable) versions; ?version=… served from bytes; ?preview=1
/api/mls/slate                 matchday scorecard (PASS/MISSED/CAPTURE_FAILED/…)
/api/mls/paper | risk | metrics
POST /api/admin/mls/sweep | corpus/publish   (operator-token gated)
```

## Launch levels (at freeze)
1 archive **READY** · 2 shadow-collection **READY** · 3
research-reproducibility **READY** (engine-matched) · 4 exec-paper **NOT**
(built; awaiting live validation + exact fee/liquidity evidence) · 5
manual-money **NOT** (empirical gate: no prospective edge established) · 6
auto-exec **NOT** (correctly absent).

## The freeze — do NOT change before the slate
model parameters · approval policy · edge thresholds · fee implementation
· execution-readiness rules · market-family policy · lock timing ·
simulation count · scheduler behavior · lineup rules · risk limits. A
critical correctness hotfix requires a NEW release tag and must be
disclosed as a protocol deviation.

Operational protocol (pre-slate freeze → T-90 go/no-go → lock-window
acceptance → invalidation conditions → 15-step post-slate) is in
[`RUNBOOK.md`](RUNBOOK.md).
