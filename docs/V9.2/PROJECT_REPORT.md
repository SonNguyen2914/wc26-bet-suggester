# Project Report — V9.2: The Execution-Fidelity Edition (July 24, 2026)

*Successor to `docs/V9.1/PROJECT_REPORT.md`. That report froze the
pre-slate baseline; this one closes the fourth-evaluation loop: a critical
order-book defect caught and fixed before the first slate, a paper layer
made exact, an audit made to validate rather than merely detect, and the
baseline re-cut to `mls-shadow-v1.2` — with money still disabled.*

## The one-liner
Extended a completed-tournament research archive into a multi-league live
platform, then — under **four** rounds of independent technical review —
built an MLS evidence chain where every new canonical T-10 lock must pass a
*validated* contract (approval hash recomputes, engine signature matches
source+runtime, best-depth exact-precision market evidence), proved the
model's construction while honestly measuring its edge over baseline as
**not established** (+0.0078, CI [−0.0126, +0.0282]), and — the last clean
moment before the first slate — fixed an order-book defect that would have
corrupted stored execution evidence, verifying the fix against a live
exchange book. All without ever opening a path to real money.

## The arc, in five movements
1. **Expansion (V8).** WC26 archive → MLS live plane on durable Postgres.
2. **Validation-ready (V9).** Two evaluations' remediation built as code:
   completeness-gated locks, retrievable artifacts, corpus, an analytic
   model-eval ladder with bootstrap CIs, real-PostgreSQL CI. Honest
   headline: the edge is within noise.
3. **P0 remediations + pre-slate observability (V9.1).** A third
   evaluation's 21 findings; the P0 set built; the evidence contract made
   enforceable and visible (`/api/mls/approval`, engine-signature replay);
   frozen as `mls-shadow-v1.1` with a self-hashed evidence record.
4. **Execution-fidelity hotfix (V9.2 / this edition).** A fourth
   evaluation found a **critical order-book defect** — the parser kept the
   worst end of Kalshi's ascending arrays. Because it corrupts *stored*
   evidence and zero locks existed, the freeze was re-opened as a disclosed
   deviation. Fixes: best-N depth, exact Decimal execution, centicent
   fees, audit hash-recompute + engine-match, source+runtime engine
   signature, provider-only executable freshness, kill-switch-aware
   readiness, load-existing approval, structural lock gate, durable
   registry record, corpus-overwrite removal. New baseline
   `mls-shadow-v1.2`.
5. **Proven against production.** The order-book fix was verified against a
   live Kalshi book: raw best NO 0.5300 → YES ask 0.4700, exact size
   37991.07, with raw == persisted == fill-engine top == prod top-of-book.
   The old code would have reported 0.84 — a catastrophic mis-price now
   demonstrably avoided.

## The cultural through-line
Adverse evidence is published, then *enforced*. `GET /api/mls/approval`
returns the confidence interval that shows approval means "safe to gather
prospective evidence, not an established edge." A lock whose approval hash
doesn't recompute, or whose engine signature doesn't match, now **fails**
its own audit. The fourth review's critical finding was accepted, verified
against the real exchange, and fixed before it could touch the first
corpus.

## By the numbers
- **446 backend tests + 5 real-PostgreSQL integration** green.
- **10 clean production PostgreSQL migrations** (head `a2b3c4d5e6f7`,
  round-tripped on real `postgres:16`).
- **Model ladder, n=162:** M2 vs baseline +0.0078, **CI [−0.0126,
  +0.0282], not significant** — live at `/api/mls/approval`.
- **Four independent evaluations.** Paper execution moved from
  "known-invalid measurement" to "exact bounded-depth taker-entry with
  general-taker fees."
- **One live-exchange diagnostic** confirming parser ⇄ fill-engine ⇄
  production agreement.

## Resume bullets
- Diagnosed and fixed, before it corrupted stored data, an order-book
  parser that retained the wrong end of an exchange's ascending price
  arrays — and *verified the fix against a live production order book*.
- Built a *validated* evidence chain: every canonical lock's approval hash
  recomputes and its source+runtime engine signature must match, enforced
  by a machine audit and real-PostgreSQL CI, under four independent
  reviews.
- Made a paper-execution layer exact end-to-end (Decimal prices/quantities,
  centicent fees, best-depth ladders) while keeping real money disabled and
  labelling the layer honestly (bounded-depth taker-entry, not full
  execution lifecycle).

## What's next (not a feature)
A clean, independently auditable slate under `mls-shadow-v1.2`, then the
post-slate sequence ending in the first immutable published corpus
([`RUNBOOK.md`](RUNBOOK.md)). The next increase in real-money readiness
comes only from prospective evidence or a demonstrably better model — the
edge is still statistically indistinguishable from zero.
