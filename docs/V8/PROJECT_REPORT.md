# Project Report — V8: The MLS Expansion (July 22–23, 2026)

*Successor to `docs/V7/PROJECT_REPORT.md` (the World Cup arc + evaluation). This
report covers the ~36 hours in which the completed-tournament archive became a
two-plane, multi-league platform with a live shadow pipeline. V7's report remains
the reference for everything WC26.*

## The one-liner

Took a hardened single-tournament research archive and, without disturbing it,
stood up a second league end-to-end in a day and a half: durable PostgreSQL evidence
store, league-fitted model that had to *earn* its shadow badge in a walk-forward
backtest, atomic full-book T-10 locks across all twelve Kalshi market families, and
a public match hub that shows model-vs-market edges on ~60 markets per fixture —
every number labeled shadow, money locked behind a gate no code path can open.

## The arc

1. **The decision (Jul 22).** An external evaluator's launch review was adopted in
   full: go live in shadow mode now; real money stays disabled until prospective
   validation; PostgreSQL is P0; approved aliases make final market attachments;
   MLS gets its own model ("do not reuse WC26 parameters"); locks are transactional
   and never reconstructed. The implementation order in that document became the
   build order.
2. **The platform day (Jul 22 evening).** MLS data layer + dashboard + match hub
   shipped in three same-night stages, verified against an in-play match (the book
   repriced seconds after a goal; the scenario engine hand-checked to the cent).
3. **The plane-birth (Jul 23, 03:00–07:00).** PostgreSQL provisioned; three crashes
   diagnosed and fixed (chronicle below); by 07:00 the live plane reported healthy
   through its own readiness endpoint.
4. **The pipeline day (Jul 23).** Identity → season ingest → model → runs → locks →
   full market coverage → four design iterations on the match hub, each deployed and
   verified live the same hour Son asked for it.

## The outage chronicle (the lesson V8 is named for)

Connecting PostgreSQL produced a three-bug chain, and the middle one mattered most:

- **Bug 1 — driver scheme.** Railway hands out `postgresql://`; SQLAlchemy then
  wants psycopg2; we ship psycopg 3. Fixed with URL normalization to
  `postgresql+psycopg://`.
- **Bug 2 — the design flaw.** The boot migration RAISED on failure. The container
  crash-looped, and Railway stopped serving *everything* — including the WC26
  archive. **25 minutes of full outage, entirely self-inflicted.** The fix became a
  law: a subordinate plane must never be able to kill the primary. Boot failures now
  disable the live plane, record `LIVE_BOOT_ERROR`, surface it in `/api/ready`, and
  a regression test (`test_live_boot_failure_never_raises`) pins it.
- **Bug 3 — the actual crash.** Alembic autogenerate rendered the partial-index
  WHERE clause as `canonical IS 1` — legal SQLite, fatal PostgreSQL. Fixed with
  explicit per-dialect SQL and a test that compiles the DDL for BOTH dialects.

Two more prod-only bugs followed the same theme (SQLite forgives, PG doesn't; local
passes, prod fails): 32-bit signed `INTEGER` overflowed by our unmasked sha-prefix
seeds (the whole run sweep died; seeds now masked to 31 bits, failures isolated
per fixture), and ESPN's schedule endpoint silently returning only *played* games
until `?fixture=true` is added (238 fixtures became 510).

## The model, honestly

`mls-2026-v0` is a deliberately small model: recency-weighted, shrunk goals rates
through the shared Monte Carlo engine, with the league scoring rate and venue split
fitted from MLS data and honest zeros everywhere no validated input exists.

The validation story is the part worth telling: **the first fit lost.** At the
initial shrinkage (k=6), the model was *worse* than a flat "every team is identical"
baseline by 0.007 logloss over 162 walk-forward fixtures. A hyperparameter sweep
showed MLS scoring is noisy enough that ratings must be pulled hard toward the mean
— k=24 beats the baseline by +0.007, stable at 4000 simulations. That number is
real but small, and it is written into the code as a standing argument for keeping
the money gate closed. `approved_for_shadow=True` was *earned*; the real-money flag
has no setter. Full tables in `docs/V8/CALIBRATION.md`.

## Full market coverage (the "Kalshi has all the markets" build)

Son's push — "Kalshi has all the market, why don't we have it?" — turned out to be
the best feature request of the arc. Discovery found 17 MLS series, 12 per-match.
Three design facts made covering all of them cheap and safe:

- Every family shares the game event's ticker suffix (`26JUL25CLBCIN`), so only the
  3-way needs name resolution; everything else joins by exact suffix.
- Every market's meaning is machine-readable in its ticker tail (`CLB4CIN2` = 4–2;
  `CIN3` = away by >2.5; `-4` = over 3.5) — no label parsing, ever.
- The simulator already emitted almost every needed probability (totals ladder,
  BTTS, margins, first-goal, full scoreline distribution); team totals fell out of
  the scorelines.

Result: prediction runs went from 3 contracts to ~35; T-10 locks freeze the whole
book; the site shows ~60 markets per match with signed edges — and they cohere (the
day it shipped, the model liked Cincinnati on the winner, the spread, AND the high
totals — one thesis expressed three ways).

## The design loop (four iterations in one day)

The first match hub was engineering-led and Son rejected it in one sentence: "too
different from WC, defeats my designs... I don't even see the every market section."
The rebuilds that followed — WC26 skeleton, then Son's own layout (info card, paired
club-color bars, folded scouting, table under the model prediction) — plus his bug
report from a phone screenshot (RBNY's book missing: a wrong ESPN name baked into
BOTH the code and its test) are the arc's product lesson: **the operator's eye found
a real data bug and a better information design faster than any review pass.**

## By the numbers

- **~36 hours** from launch decision to full-coverage shadow platform.
- **399 backend tests** (327 at V7), 119 commits backend / 114 frontend.
- **510 fixtures**, 30 clubs, **652 Kalshi events / 12 families** discovered,
  **~35 contracts per prediction run**, 15-fixture Saturday slate fully priced.
- **5 backend deploys in 24h**, archive intact through all of them; **1 outage
  (25 min)**, converted into the plane-isolation law + regression test.
- **+0.0073** walk-forward logloss edge vs flat baseline (n=162) — the whole reason
  "shadow" is the only mode.

## Resume bullets (ready to paste)

- Extended a single-tournament prediction platform into a multi-league system by
  designing a two-plane architecture (immutable tournament archive + durable
  PostgreSQL live plane) where a live-plane failure provably cannot degrade the
  archive — validated by regression tests and five production deploys in 24 hours.
- Built a league-specific Monte Carlo model gated by rolling-origin validation;
  rejected the initial parameterization when it underperformed a naive baseline and
  shipped only the configuration that beat it, with the (small) edge documented as
  grounds for keeping real-money features disabled.
- Implemented a full-book market evidence chain: approved-alias identity resolution,
  exact ticker-suffix mapping across 12 market families, integer-cent quote + depth
  capture, and atomic pre-kickoff locks enforced by partial unique indexes.
- Diagnosed and fixed cross-dialect production failures (SQLite-vs-PostgreSQL DDL,
  32-bit integer overflow) and codified each as a dual-dialect regression test.

## Links

- Live: https://namson.dev/bet-suggester?league=mls (board) → any match card (hub)
- Readiness: https://wc26-bet-suggester-production.up.railway.app/api/ready
- Docs: `docs/V8/` (this arc), `docs/V7/` (evaluation arc), `docs/V6/` (tournament)

---

## V8.1 ADDENDUM — the P0 remediation (July 23, 2026, evening)

A second independent evaluation of the V8 zip arrived the same day and was adopted in full. It found the architecture sound but the MLS evidence chain not yet atomic — the sharpest finding provable from our own test: a canonical T-10 lock could complete with zero captured quotes. All twelve P0 items were fixed and deployed (@48837bf backend, @2a06214 frontend); 408 tests green; the batch-mode migration `8329bc9afacb` ran clean on **prod PostgreSQL** — the real-Postgres migration the evaluator noted had never been done.

| # | Finding | Fix |
|---|---|---|
| F1 | Lock not atomic (quotes separate, could be zero) | `MarketSnapshot` header + completeness gate; `capture_lock_snapshot` fetches all externally, one transaction, `status='complete'` only when every event returned and the 3-way is priced; **no complete snapshot → no canonical lock** |
| F2 | Provenance columns null | runs freeze `model_version_id`, `input_snapshot_hash`, `market_snapshot_id`; every contract links its frozen `market_quote_id` (35/35 verified) |
| F3 | Approval was metadata | `scheduled_runs`/`t10_locks` fail closed without an approved `ModelVersion`; boot approves before running |
| F4 | Team totals from truncated scorelines | computed from the simulator's **full goal arrays** (verified live: home O1.5 0.526 vs 0.317 truncated) |
| F5 | Current Kalshi schema dropped | parse `*_size_fp`/`volume_fp`/`open_interest_fp`/`updated_time`/rules + `orderbook_fp` depth (verified live: 62 quotes all sized + 833 depth rows, was zero) |
| F6 | No pagination | `_kalshi_paged` cursors to exhaustion; correct-score events capture fully |
| F9 | Later run supersedes lock | `model_for_event` exposes `primary` = the lock once it exists; sweep is pre-match-status only and skips locked fixtures; frontend renders `primary` |
| F10 | Seed on mutable row id | `seed_for` hashes the ESPN event id (survives rebuild; regression test) |
| F11 | Fixed UTC−4 breaks post-DST | `ZoneInfo("America/New_York")` in both market paths |
| F12 | Readiness didn't measure operation | `shadow_counts` adds `shadow_ready` + named `blockers` |
| — | Gross gap labeled "Edge" | table column is **Net edge** = model − (ask + fee) |
| — | `UNIQUE(run, market_contract_id)` skipped NULLs | added `UNIQUE(run, outcome_key)`; hardcoded test path removed |

**Live proof (prod, CLB-CIN):** one real lock snapshot captured 62 quotes — all with sizes, 833 depth rows — across 11/11 events, produced a canonical lock with full provenance and all 35 contracts joined to frozen quotes. `/api/ready` reports `shadow_ready: true, blockers: []`.

**Still open (evaluator's P1/P2, not blocking shadow):** a reproducible MLS corpus export, common-random-number backtesting with uncertainty intervals, PostgreSQL integration tests in CI, raw-payload object storage, and the team/player/availability/lineup snapshot inputs before any real-money discussion. Saturday's locks are now **provenance-complete canonical evidence**, still shadow-labeled, money still locked.

### V8.1 re-evaluation + the qualification build (later Jul 23)

The re-evaluation upgraded the verdict conditionally — T-10 evidence integrity 3.5→8.4, the central objection ("a canonical lock can exist with zero quotes") declared closed — and left three qualifications to preserve. Two of them were built the same day (@84158a6, migration `9673668959a8`, third clean prod-PG migration):

- **Qual #2 — capture-complete vs execution-ready are different states.** `MarketSnapshot.status` stays capture-completeness only; a new `execution_ready` flag is derived separately (the game 3-way must be two-sided AND fresh). A no-bid contract is now explicitly a *complete observation with a null bid* — never an incomplete capture, never an invented price. Live proof: a fresh lock's snapshot captured 62 priced quotes with `required_families_complete=true` but `execution_ready=false` because the thin MLS book hadn't updated recently — capture and tradeability, honestly distinct.
- **Qual #3 — "full book" needs a versioned policy.** The snapshot gained `policy_version` (`mls-lock-v1`), the priced/unpriced quote split, and `oldest_quote_age_seconds`, so the lock predicate can't change meaning silently as families are added.
- **Approval immutability** — `prediction_run.model_approved_at_run` freezes whether the model was approved *at capture*; flipping the flag later can't re-authorize an old run.

And the **acceptance-audit harness** the evaluator elevated above more building: `GET /api/mls/audit` (public read-only, content-hashed) checks every canonical lock against the full invariant table — one lock per fixture, before kickoff, inside window, snapshot complete + policy-versioned + required families, contracts unique by outcome, every priced contract quote-linked, model approved-at-run, seed/hash present, three-way sums to one, no post-kickoff replacement — and **retains** missed locks and failed snapshots as evidence. It caught a pre-manifest lock as failing in rehearsal, which is the point. Live now, showing 0 locks / clean until Saturday populates it.

**Qual #1 (a retrievable input document, not just a hash) was deliberately deferred** — it is the P1 corpus-export work, and the docs no longer claim "independently model-reproducible", only "market-provenance-complete".

### Phase 2 — the retrievable input artifact (Jul 23, last build)

The re-evaluation's full launch-gate roadmap (six levels, sixteen phases) named ONE immediate task: the retrievable canonical input artifact — the last provenance gap, and the qualification #1 deliberately deferred earlier. Built and deployed before Saturday (@4f3ab19, migration `c21ba2ee8df4`, the 4th clean prod-PG migration):

`input_snapshot_hash` proved integrity but not reproducibility, because the bytes it hashed weren't kept. Now every run stores a `model_input_artifact` — the exact canonical document it simulated from (model params, fitted league params, both teams' ratings, seed, draw count, the source-fixture provider ids that fed the fit, and the data cutoff), deduped by content hash, deterministically serialized (sorted keys, float round-trip, UTC, no machine paths). `replay_from_artifact` reconstructs the engine inputs from that document ALONE and re-runs the sim with the frozen seed; `GET /api/mls/replay/{run_id}` (public) does it live.

**Live proof, prod:** a real run replays with `max_delta: 0.0` — bit-identical reproduction from the stored bytes, no live database touched. This moves the project across the roadmap's second major launch level:

- **Level 3 — Research reproducibility:** *now reached* (was "not ready"). The project can accurately say **independently model-reproducible**: hand someone the artifact document and the seed, they get the same probabilities.

The claim language is updated accordingly — "market-provenance-complete AND independently model-reproducible." What remains is genuinely the slower science the roadmap lays out: Levels 4–6 (execution-quality paper trading, manual real-money, automated execution), each gated on prospective evidence, lineup-quality inputs, a risk engine, and validated fills — none of it emergency, none of it blocking Saturday's first provenance-complete, independently-reproducible locks.
