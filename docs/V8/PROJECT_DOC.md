# WC26 Bet Suggester → Multi-League Platform — Project Documentation & Roadmap

> **SUPERSEDED BY V9** (`docs/V9/PROJECT_DOC.md`, Jul 23) — V8 remains the
> expansion snapshot; the evaluation-remediation-through-roadmap arc (atomic
> evidence chain, reproducible inputs, corpus, model ladder + CIs, paper
> trading, risk engine, observability, slate audit) lives in V9.

**V8 — July 23, 2026. THE EXPANSION EDITION.** V7 closed the evaluation arc and froze
the World Cup as a hardened read-only archive. V8 opens the next one: in the ~36 hours
after V7 was written, the project **became a two-plane platform** — the WC26 archive
untouched underneath, and a brand-new **MLS live plane** on top: durable PostgreSQL,
a league-fitted model (`mls-2026-v0`) earning its shadow approval through a rolling-
origin backtest, an evidence chain that freezes **every Kalshi market family** (not
just the 3-way) at T-10, and a match hub rebuilt three times in one day against the
operator's design instincts until it earned them. Money remains locked: **every model
number on the public site is labeled shadow, and no code path can produce a real-money
recommendation.** Docs live in `docs/V8/`; V7 remains as the evaluation-closing
snapshot; V6 the tournament close; V5 survives on branch `docs-v5-handoff`.

---

## ⚡⚡⚡⚡ CURRENT STATE — V8 SNAPSHOT (July 23, 2026)

### The one-paragraph version
One backend, two planes. The **archive plane** is V7's system exactly: fail-closed
read-only WC26 research archive, self-healing at every deploy, 16/16 results, 84/84
ledger, 6/6 canonical lock bundles — a live-plane failure cannot touch it (proven the
hard way; see the outage chronicle in PROJECT_REPORT). The **live plane** is new:
Railway PostgreSQL (with a volume — the project's first durable storage), Alembic-
managed schema, ESPN-fed fixtures for the whole MLS 2026 season, an approved-alias
identity table bridging ESPN and Kalshi names, discovery + integer-cent quote capture
across **all 12 per-match Kalshi families**, and a shadow model whose runs write a
provenance-complete contract for every market it prices (~35 per fixture). The public
site serves it all at namson.dev/bet-suggester (`?league=mls`): shadow odds chips on
the board, and a per-match hub with market-vs-model comparison bars in club colors,
an every-market edge table, fitted-ratings scouting, and ESPN live stats.

### Where things stand right now
- **Repos:** backend `~/dev/wc26-bet-suggester` @ `6e7bb31` (**399 tests green**, 119
  commits, ~10.9k LOC src+api+jobs, ~5.7k LOC tests); frontend `~/dev/namson-dev`
  @ `b3dfce5` (114 commits). Local == remote == deployed on both.
- **Prod:** Railway backend `wc26-bet-suggester-production.up.railway.app` — `/api/ready`
  reports BOTH planes (`ready: true`; archive 16/84/6; live `connected`,
  `migrations_current`, `competition_seeded`, shadow counts). Frontend on Vercel.
- **Live DB durability: PROVEN.** 510 fixtures + 30 teams + 99 mapped events written
  by one container were read back by the next deploy (write→restart→read across a
  restart). MLS T-10 locks survive deploys — the WC26 "unrestorable class" caveat is
  historical only.
- **Modes:** `COMPETITION=mls-2026`, `MLS_SHADOW_ENABLED=true`,
  `REAL_MONEY_SIGNALS_ENABLED=false`, `AUTO_EXECUTION_ENABLED=false`. Flag parsing is
  fail-safe (unknown value → the safer mode). `approved_for_real_money` is a DB column
  no code path sets.
- **First real test:** Saturday July 25 — a 15-fixture slate (22:30–02:30 UTC), the
  first canonical T-10 locks with full-book freezes and PAPER-labeled alerts.

### The day in one list (what V8 added over V7)
1. **Plane isolation as law** — live boot failures disable the live plane and report
   via `/api/ready`; they can never crash-loop the archive again (regression-tested
   after a 25-minute self-inflicted outage taught the lesson).
2. **PostgreSQL live plane** — psycopg3 URL normalization, Alembic baseline + 2
   migrations, partial unique index enforced identically on SQLite (tests) and PG
   (prod), dual-dialect DDL compilation test after `canonical IS 1` killed migration #1.
3. **Identity** — 30 clubs seeded from ESPN; curated Kalshi bridges as APPROVED alias
   rows; fuzzy may propose, only the alias table attaches markets.
4. **Season ingestion** — full 2026 MLS season (both ESPN schedule halves: bare
   endpoint = played, `?fixture=true` = upcoming), reschedules as history rows,
   content-hashed source observations, scores frozen once.
5. **`mls-2026-v0`** — recency-weighted shrunk goals rates through the SHARED Monte
   Carlo engine; league gpg + venue split FITTED from MLS data (WC26 values are
   structure, never coefficients); set-piece neutral; honest zeros for form/fatigue;
   `SHRINK_GAMES=24` chosen by walk-forward sweep after k=6 LOST to the flat baseline.
   `approved_for_shadow=True` (earned: +0.007 logloss edge, n=162), money flag untouched.
6. **Prediction runs** — UUID batches, `writing→complete` status gating (readers only
   ever see complete), deterministic 31-bit seeds (a 32-bit seed overflowed PG's
   signed INTEGER and emptied the board — fixed with a regression test), display
   payload frozen WITH the run, per-fixture failure isolation.
7. **Atomic T-10 locks** — book freeze first, transactional run, canonical flag under
   a partial unique index, alert only after commit; a failed lock stays visibly missing.
8. **Full market coverage** — 17 MLS series discovered, 12 per-match families mapped
   by exact ticker-suffix join to the game event; outcome keys parsed from
   machine-readable ticker tails (`CLB4CIN2` = score 4–2, `CIN3` = away by >2.5);
   the model prices totals 0.5–5.5, BTTS, margins, first-goal, team totals, and 12
   scorelines; runs carry ~35 contracts each.
9. **Operator surface** — `POST /api/admin/mls/sweep?force=1` (token-gated) runs
   window/map/runs sweeps synchronously and returns their result dicts: remote
   diagnostics that would have turned a three-deploy debug into one call.
10. **The site** — MLS board with shadow chips + league deep-linking
    (`?league=mls`); match hub in the operator's final layout (info card, xG duel,
    ratings scouting, ESPN form/H2H both folded, paired three-way bars in club
    signature colors, every-market table under the model prediction, scenario
    engine, state-aware live stats). Four design iterations in one day, each shipped
    and verified live.

---

## PART A — ARCHITECTURE (V8 delta map; archive plane unchanged from V7 Part A)

### The two-plane law
One FastAPI process, two completely separate storage planes:

| | Archive plane (WC26) | Live plane (MLS) |
|---|---|---|
| Storage | SQLite in-container, ephemeral | Railway **PostgreSQL + volume**, durable |
| Schema | `src/db.py`, created at boot | `src/live/models.py`, **Alembic-managed** |
| On deploy | Self-heals from committed artifacts | Persists; migrations run once at boot |
| On failure | — | **Disables itself**, archive keeps serving |
| Writes | Operator-token only (fail-closed) | Scheduler jobs only |

`src/live/db.py` is the gatekeeper: `live_enabled()` (URL present),
`plane_ready()` (enabled AND boot didn't fail), `LIVE_BOOT_ERROR` (the recorded
failure, surfaced by `/api/ready`), `migrate_and_seed()` (subprocess
`alembic upgrade head` + idempotent competition seed, catches EVERYTHING).

### Live plane modules (`src/live/`)
- `models.py` — 13 tables: competition, team, team_alias, fixture (+score columns),
  fixture_change, source_observation, model_version, prediction_run (+payload_json),
  prediction_contract, market_event, market_contract, market_quote (integer cents,
  both sides, sizes), market_depth_level. The load-bearing index:
  ONE canonical complete t10 per fixture, partial-unique on BOTH dialects.
- `identity.py` — ESPN seeding + `KALSHI_BRIDGES` (30 curated, e.g. `"New York RB"
  → "Red Bull New York"` — ESPN's word order, learned live); `resolve()` consults
  approved rows only.
- `ingest.py` — per-club season schedules (two calls each: played + `fixture=true`)
  + rolling scoreboard window (−7d..+14d); `FixtureChange` history for kickoff moves
  >60s and status transitions; SHA-256-hashed raw payloads.
- `markets.py` — `FAMILY_SERIES` (12) discovery with a 0.25s-gap throttle (Kalshi
  429s bursts); GAME anchors fixture mapping via aliases, families suffix-join;
  contracts self-heal NULL outcome keys; `capture_quotes` snapshots every mapped
  family (cents + depth) inside a 48h pre-kickoff horizon.
- `model_mls.py` — see Part B.
- `runs.py` — see Part C.

### Scheduler (`jobs/scheduler.py` — archive jobs unchanged)
MLS jobs are lazy-imported and `plane_ready()`-gated, registered unconditionally
(instant no-ops when off): `mls_window` 15m, `mls_markets` 10m, `mls_runs` 15m
(168h horizon / 4h freshness), `mls_t10` 60s. `mls_boot` is its OWN one-shot —
never chained into the archive boot sequence (isolation, again).

### API (V8 additions)
- `GET /api/mls/odds` — the shadow board (every upcoming fixture's newest complete run).
- `GET /api/mls/match/{id}` — match + `books` (grouped families with per-row
  `model_key`) + `model` (latest run with provenance + t10 lock state).
- `POST /api/admin/mls/sweep?force=1` — operator sweeps, results returned.
- `/api/ready` — now reports `mode`, `real_money_signals`, and `live.shadow` counts
  (teams / fixtures / completed / complete_runs / t10_locks / mapped_events).

---

## PART B — THE MLS MODEL (`mls-2026-v0`)

**Design:** interpretable goals-rate baseline through the SHARED `MatchSimulator`.
- attack_i / defence_i = recency-weighted (90-day half-life) GF/GA per game relative
  to league, shrunk toward 1.0 with prior weight **k=24 games**.
- `league_gpg` and home/away venue multipliers **fitted from MLS 2026 data** (engine
  generalization: `predict_xg` reads optional `league_base` / `venue_mult`; absent →
  exact WC26 behavior, pinned by tests).
- Set-piece term NEUTRAL (threat == baseline → centered adjustment 0), form 0.5,
  fatigue 0, equal elo — honest zeros where no validated MLS inputs exist.
- Deterministic seeds: `sha256(model:fixture:run_type)[:8] & 0x7FFFFFFF` (31-bit —
  PG's INTEGER is signed).
- `MIN_GAMES=5` before a team is rated; unknown team → **no prediction**, never a
  default-stats guess.

**Validation (full detail in `docs/V8/CALIBRATION.md`):** rolling-origin walk-forward
over 162 completed fixtures. k=6 (the initial guess) LOST to a flat league-average
baseline by 0.007 logloss; the sweep found a stable optimum near k=24 (+0.007 edge,
confirmed at 4000 sims). The edge is real but SMALL — written into the code as a
standing reason the money gate stays closed. `approved_for_shadow=True` recorded on
the `model_version` row; `approved_for_real_money=False`, settable only by a future
evidence-review gate.

**Probability surface per run:** 3-way outcomes; totals ladder over 0.5–5.5; BTTS;
winning margins (≥2, ≥3 both sides — Kalshi's "spread"); first team to score + no
goal; team totals (summed from the scoreline distribution); 12 scorelines; xG.
Method-of-victory and 1st-half families are market-only (the simulator emits halves
data — an evidence-ready extension).

---

## PART C — THE SHADOW EVIDENCE CHAIN

Every batch is a `prediction_run`: UUID id, run_type (`scheduled|t10`), status
gating (`writing → complete|failed`; **readers only see complete** — enforced in
every query, no time-window reconstruction anywhere), stored seed + sim count + git
revision, frozen display payload (xg/scorelines/props/basis — recomputing later
against refreshed ratings would silently diverge from the stored contracts).

Contracts: the 3-way ALWAYS; plus one row for **every mapped market contract whose
outcome key the run prices** (~35/fixture on the current slate). Outcome keys come
from ticker-tail parsing (`src/mls.py::model_key_for`), never label text.

**The T-10 lock** (`runs.t10_locks`, 60s sweep): fixtures 0–11 min from kickoff
without a canonical lock → (1) freeze the full book (`capture_quotes` → integer-cent
quotes + depth, hash-chained observation), (2) transactional run, (3) validations,
(4) `complete + canonical` COMMIT — the partial unique index makes a second canonical
lock physically impossible — (5) only then a PAPER-labeled alert. A crash before
commit leaves nothing visible; a missing lock stays visibly missing.

---

## PART D — MARKET COVERAGE (the full Kalshi surface)

17 MLS series exist; 12 are per-match. All 12 are discovered, mapped, captured, and
shown: GAME, TOTAL, BTTS, SPREAD, TEAMTOTAL, SCORE, FTTS, MOV, 1H, 1HTOTAL,
1HSPREAD, 1HBTTS (futures: CUP/EAST/WEST; JOIN/ADVANCE ignored).

**The mapping trick:** every family shares the game event's ticker suffix
`{YY}{MON}{DD}{HOME}{AWAY}` — so only GAME needs name resolution (approved aliases);
everything else inherits its fixture by exact suffix join. Tail parsing handles the
rest: `-3` on TOTAL = over 2.5; `CLB2` on SPREAD = home by >1.5 (home-first prefix
matching survives NYRB/NYC-style near-collisions); `CLB4CIN2` on SCORE = 4–2.
The site shows ~60 markets per match, each with model likelihood, signed edge,
payout multiple, and both sides of the book.

---

## PART E — THE SITE (namson.dev/bet-suggester)

- **Board:** league carousel with URL deep-linking (`?league=mls` — the league is in
  the URL, so refresh/share/back stay in the right room). MLS mode: today's slate
  with H/D/A shadow chips, real KXMLSGAME books, 7-day fixture list with inline odds,
  standings.
- **Match hub** (`/bet-suggester/mls/{eventId}`) — the operator's final layout, in
  order: compact match-info card (kickoff, venue, live countdown, crests, badges);
  xG duel (cards + shares bar in home club color); "How they play" (fitted
  attack/defence ×league + form chips — data lines, no narratives; folded);
  ESPN form + H2H (perspective-first scores — "L · RBNY 1–6 CLT · away at CLT";
  folded); **market-vs-model as two ALIGNED three-way stacked bars** — home segment
  in the club's signature color (ESPN palette, luminance-checked), draw neutral,
  away in theirs — read vertically to see where they disagree; Model prediction
  (scoreline grid + chance chips); **the every-market table** (grouped, foldable
  families; correct-score and 1H start folded); scenario engine (fee-aware,
  price-only); ESPN live stats + timeline (bottom pre-match, promoted under the
  info card when in play). Everything model-made carries the shadow label.

---

## PART F — OPS RUNBOOK

- **Read prod state:** `GET /api/ready` — one JSON tells you both planes' health and
  the shadow counts. This endpoint has already paid for itself twice.
- **Force the pipeline:** `curl -X POST ".../api/admin/mls/sweep?force=1" -H
  "X-Admin-Token: $(cat ~/.wc26_admin_token)"` — refresh window, remap markets,
  regenerate runs; results in the response body.
- **Kalshi budget:** all live-plane calls go through a 0.25s-gap throttle; site-layer
  family fetches sleep 0.1s between series. Discovery 429s on cold sweeps are normal —
  the 10-minute cycle backfills (all counters idempotent).
- **Gotcha ledger (each cost a debugging loop; don't relearn):** PG `Integer` is
  signed 32-bit (seeds masked); `canonical IS 1` is SQLite-only DDL (per-dialect
  `text()` + compile test); ESPN schedule endpoint hides upcoming games behind
  `?fixture=true`; ESPN says "Red Bull New York"; Kalshi in-play events leave the
  `open` status while their markets stay `active` (judge tradability per MARKET);
  correct-score events need `limit≥30`; Next 16's `next-server` daemon survives
  `pkill next dev` with stale env; browser-pane tabs wedge Next hydration after
  cross-origin hops (verify prod in a FRESH tab).

## PART G — MODES & THE MONEY GATE

Three modes, fail-safe parsed (`config._parse_flag`: unknown → safer):
- **MLS_SHADOW (on):** ingest, capture, predict, lock, alert (PAPER-labeled). No
  recommendations anywhere.
- **MLS_MANUAL (off):** would surface recommendations for manual execution — blocked
  until the model passes PROSPECTIVE validation (T-10 locks scored against results).
- **MLS_AUTO (off):** stays off.

The gate is layered: env flag off, `approved_for_real_money=False` with no setter,
frontend copy states it on every model surface, and the model's own backtest
docstring records why (+0.007 logloss is not an executable edge).

## PART H — KNOWN DEBT & NEXT (honest, ordered)

1. **O9 paper trading** — signal/paper_position/paper_fill/settlement tables + logic
   riding the T-10 locks. The largest remaining decision-item.
2. **Prospective scoring** — settle lock contracts against results (fixture scores
   already ingest); publish Brier vs the frozen book per family.
3. **Roster/availability/lineup snapshots** (tranche 2) + the lineup panel; live-read
   (in-play repricing) for MLS — the WC26 machinery exists, needs MLS wiring.
4. **1st-half model** — simulator halves output exists, unused.
5. Minor: `prediction_run.model_version_id` never populated; API-Football MLS ids
   undiscovered (free-plan season-blindness needs verification first — see memory).
6. **EPL / La Liga** (mid-Aug window): the whole live plane was built
   competition-generic — new slug, new ESPN league path, new alias bridges, new
   Kalshi series list; the match hub template is `@3aaf2f8`.

## PART I — REPORT CARD (V8, one screen)

- Two planes, one process, zero archive regressions: **399 tests green**, archive
  16/84/6 intact through five deploys in 24h.
- Durability: proven write→restart→read on Railway PG.
- Model: honest — shipped only after beating the flat baseline, labeled small-edge,
  shadow-only.
- Evidence: every run seeded + hashed + status-gated; locks atomic; full-book capture
  in integer cents across 12 families.
- The site shows ~60 markets per match with model-vs-market edges — the thing the
  whole project exists to compare — and says "shadow, not advice" on every one.
