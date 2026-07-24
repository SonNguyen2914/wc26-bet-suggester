# WC26 → Multi-League Platform — Project Documentation (V9.2)

**V9.2 — July 24, 2026. THE EXECUTION-FIDELITY EDITION.** V9.1 froze the
pre-slate baseline; V9.2 records what a *fourth* independent evaluation
found — a **critical order-book defect** that would have corrupted the
first slate's stored execution evidence, plus execution-fidelity and
governance gaps — and the disclosed hotfix that closed them before any
lock existed. The slate-producing baseline is now **`mls-shadow-v1.2`**.
Every new canonical lock carries a *validated* evidence contract; paper
execution is exact and provenance-enforced; and every path to real money
stays closed. Docs live in `docs/V9.2/`; V9.1 is superseded (frozen
pre-slate snapshot); the fourth-eval response is
[`docs/V9/V1.2-REMEDIATIONS.md`](../V9/V1.2-REMEDIATIONS.md).

---

## ⚡ CURRENT STATE — V9.2 SNAPSHOT

### One paragraph
One backend, two isolated planes. The **archive plane** is the completed
WC26 record: fail-closed read-only, self-healing (16 results / 84 ledger /
6 lock bundles). The **live plane** is the MLS shadow platform on durable
Railway PostgreSQL. Each canonical **T-10 lock** must satisfy, as
*enforced audit invariants*: a persisted CI-based **approval decision**
whose content hash **recomputes** and whose **engine signature matches**
the current engine; a `model-input-v3` artifact whose engine signature
fingerprints the model/simulator **source + runtime**; a completeness-
gated **market snapshot** with **best-10-per-side** depth captured at
**exact** provider precision and **provider-timestamped** freshness for
executability; the **lineup it saw** (or an explicit fetch-failure
snapshot); and a frozen quote per priced contract. Paper execution walks
that depth in **exact Decimal** with **centicent** general-taker fees.
Readiness is mode-specific and, for paper, splits engine-operational from
new-entries-allowed (gated on kill switches). **Real-money signals are
disabled and no code path can enable them.**

### The frozen baseline
```text
release:            mls-shadow-v1.2
backend:            f875c6f   (deployed f4e5581e + v1.2 evidence)
frontend:           2eed3ad   (unchanged from v1.1)
alembic head:       a2b3c4d5e6f7
model version:      mls-2026-v0
approval decision:  id 4 / hash 79c32d7d…3546b4e4  (hash RECOMPUTES)
engine signature:   d18f8bf0…4b298004  (source + runtime; model-input-v3)
real-money signals: false
```
Self-hashed record:
[`docs/V9/PRE-SLATE-EVIDENCE-v1.2.md`](../V9/PRE-SLATE-EVIDENCE-v1.2.md);
release manifest: [`RELEASE-mls-shadow-v1.2.md`](RELEASE-mls-shadow-v1.2.md).

### The controlling statistical fact (unchanged)
```text
M2 vs baseline: +0.0078 log-loss   95% CI [-0.0126, +0.0282]   n=162   NOT significant
```
Live at `GET /api/mls/approval`. Shadow approval means *safe and coherent
enough to gather prospective evidence* — never *approved for financial
recommendations*. The CI still crosses zero, so the money gate stays shut.

### Verified against a REAL live book (V9.2)
The order-book fix was proven against a live Kalshi book
(`KXMLSGAME-26JUL25NEATL-NE`, read-only, no lock created):
```text
RAW best NO bid 0.5300 (size 37991.07)  → implied YES ask 0.4700
PARSED best (best-10 kept, 0.01–0.10 tail dropped) = 0.5300 / 37991.07
FILL-ENGINE ladder top = 0.4700 / 37991.07
raw == persisted == fill-engine top == prod top-of-book (0.47)  ✓
(the old [:10] would have reported 0.84 — a catastrophic mis-price)
```

## The evidence / integrity contract (what V9.2 hardens)

For every `mls-shadow-v1.2` canonical lock, the audit ENFORCES (`all_pass`):
- **Authorized** — references a persisted `ModelApprovalDecision`; the
  audit **recomputes** its content hash from the stored canonical
  document and requires a match (V9.1 eval F4), and the run cannot be
  created without it (F9).
- **Reproducible under the correct engine** — the artifact's
  `model-input-v3` engine signature (source + runtime, F5) must **equal**
  the current engine (F4); replay refuses on drift.
- **Faithfully captured** — best-10-per-side depth (F1), exact fixed-point
  prices/sizes (F2/F7), provider-timestamped freshness for executability
  (F6), against a completeness-gated snapshot with a durable
  registry-discovery record (F10).

## Endpoint surface (public read-only; mutations fail-closed)
```
/api/ready                 archive / shadow_collection / paper_engine_operational / paper_new_entries_allowed (+ kill switches)
/api/mls/match/{eventId}   match hub: model + current book + temporal basis
/api/mls/approval          the STORED approval decision (no recompute)
/api/mls/replay/{run_id}   replay + stored/current engine signature + engine_match
/api/mls/audit             lock integrity — approval hash + engine match VALIDATED
/api/mls/corpus            published (immutable) versions; ?version served from bytes; ?preview
/api/mls/slate | model-eval | odds | paper | risk | metrics
POST /api/admin/mls/sweep | corpus/publish     (operator-token gated)
```

## Launch levels (at freeze)
1 archive **Ready** · 2 shadow-collection **Ready** · 3
research-reproducibility **Ready** (source+runtime signature; not a
container digest) · 4 exec-paper **bounded-depth taker-entry, exact
prices/qty/general-taker fees — under prospective validation** · 5
manual-money **Not approved** (empirical gate: edge indistinguishable
from zero) · 6 auto-exec **correctly absent**.

## The freeze — do NOT change before the slate
model parameters · approval policy · edge thresholds · fee arithmetic ·
execution-readiness rules · market-family policy · lock timing ·
simulation count · scheduler · lineup rules · risk limits. A further
critical hotfix requires a NEW tag + a disclosed deviation (as v1.2 was).
Operational protocol is in [`RUNBOOK.md`](RUNBOOK.md).
