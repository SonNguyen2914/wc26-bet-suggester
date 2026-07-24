# Project Report — V9.1: The Frozen Pre-Slate Edition (July 24, 2026)

*Successor to `docs/V9/PROJECT_REPORT.md`. That report told the arc up to
the validation-ready edition; this one closes it: a third independent
evaluation, the P0 research-integrity remediations it prescribed, a
pre-slate observability patch, and a new frozen release baseline —
`mls-shadow-v1.1` — locked for its first prospective matchday, with money
still disabled.*

## The one-liner
Extended a completed-tournament research archive into a multi-league live
platform, then — under **three** rounds of independent technical review —
rebuilt its MLS evidence chain until every new canonical T-10 lock must
reference, as *enforced integrity invariants*, a persisted CI-based
statistical approval decision, a versioned model-input artifact, a
matching engine signature, and frozen market evidence; proved the model's
construction while honestly measuring that its edge over a naive baseline
is **not established** (+0.0077, CI [−0.0119, +0.0286], n=162); and froze
the whole system for a clean prospective slate — without ever opening a
path to real money.

## The arc, in four movements
1. **Shadow prototype → expansion (V8).** WC26 archive extended into an
   MLS live plane on durable PostgreSQL; the first evaluation found the
   architecture sound but the evidence chain not yet provably intact.
2. **Validation-ready (V9).** Two evaluations' remediation-plus-roadmap
   built as code: completeness-gated locks, retrievable input artifacts,
   a self-contained corpus, an analytic model-eval ladder with bootstrap
   CIs, depth-aware paper trading, a central risk engine, real-PostgreSQL
   CI. Honest headline published: the edge is within noise.
3. **P0 remediations (this edition).** A third evaluation raised 21
   findings — accurate and fair. The P0 set was built and deployed:
   persisted CI-based approval (no more point-estimate boot gate),
   lineup-failure snapshots, provenance foreign keys, cursor-complete
   discovery, exact fixed-point capture, explicit freshness basis, the
   correct order-level Kalshi fee, immutable published corpus, a
   frozen-engine replay guard, mode-specific readiness, frontend temporal
   labeling — with several claims **narrowed** to exactly what the code
   proves.
4. **Pre-slate observability + freeze.** Three closures made the evidence
   contract enforceable and externally visible: `GET /api/mls/approval`
   (the stored decision, no recompute); the approval-decision reference
   as a *required* lock audit invariant; the engine signature surfaced in
   replay + audit. Then a self-hashed pre-slate evidence record, a new
   frozen tag, and a full deploy freeze.

## The cultural through-line
Adverse evidence is published, not hidden — and now *enforced*. The
runtime no longer merely says "approved": `GET /api/mls/approval` returns
the confidence interval that shows the approval means **"suitable for
prospective shadow collection, not evidence of a betting edge."** A lock
that cannot show its approval decision and matching engine signature now
*fails* its own audit.

## By the numbers
- **443 backend tests + 5 real-PostgreSQL integration** green (the one
  network-dependent lineup test made hermetic).
- **9 clean production PostgreSQL migrations** (head `f9a1c0d2b3e4`,
  round-tripped empty→head→down on real `postgres:16`); the pre-slate
  observability patch added **no** migration. Archive never blinked
  through ~16 deploys; 1 outage (25 min) → a law + test.
- **Model ladder, n=162:** M2 vs baseline +0.0077, **CI [−0.0119,
  +0.0286], not significant** — the number the whole gate rests on, now
  live at `/api/mls/approval`.
- **Three independent evaluations.** T-10 integrity: prototype → central
  objection closed → approval + engine evidence enforced.

## Resume bullets
- Built a provenance-complete, *enforceable* evidence chain: every
  canonical lock references a persisted CI-based approval decision, a
  versioned model-input artifact with a frozen engine signature, and
  completeness-gated market evidence — verified by a machine audit and
  real-PostgreSQL CI, under three rounds of independent review.
- Shipped an observability layer that makes a statistical-governance
  decision and a reproducibility guarantee independently auditable
  (`/api/mls/approval`, engine-signature replay), then froze the release
  behind a self-hashed pre-slate evidence record.
- Held a hard scope boundary: closed research-integrity gaps without
  touching model forecasting behavior, and kept real money disabled.

## What's next (not a feature)
A clean, independently auditable slate produced under `mls-shadow-v1.1`
with no unrecorded deviations — see [`RUNBOOK.md`](RUNBOOK.md). The next
increase in real-money readiness comes only from prospective slates that
survive realistic fees, liquidity, uncertainty, and correlated exposure —
not from another build.
