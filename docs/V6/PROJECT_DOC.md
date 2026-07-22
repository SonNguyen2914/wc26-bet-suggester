# World Cup 2026 Kalshi Bet Suggester — Project Documentation & Roadmap

**V6 — July 21, 2026. THE FINAL EDITION.** The tournament is over: **Spain are world
champions, 1–0 AET over Argentina (Ferran Torres, 106')** — and the model called it at the
T-10 freeze (Spain 53.9%). This document closes the tournament arc that V1–V5 opened: the
complete system as deployed, the final 12-bot leaderboard and its verdict on the edge
thesis, the research archive in its finished state, everything fixed since V5, and the
road from here (post-tournament harvest + the next-league platform). Per the V5.1 standing
rule, this doc lives IN THE REPO (`docs/V6/PROJECT_DOC.md`); V1–V4 remain ghosted with the
Desktop, V5 survives at `docs/PROJECT_DOC_V5.md` (branch `docs-v5-handoff`).

---

## ⚡⚡⚡⚡ CURRENT STATE — V6 SNAPSHOT (July 21, 2026)

### The one-paragraph version
Son's personal research tool that priced every Kalshi World Cup 2026 market against a
Monte Carlo match simulator, live-repriced matches in play from real feeds (with
play-by-play momentum), fired buy/sell alerts to Discord + phone push, ran a **twelve-bot**
paper-trading strategy laboratory (controls included), tracked Son's REAL positions with
hold-vs-cashout EV verdicts, and archived locked-model-vs-closing-line data for every
knockout match. Backend Python/FastAPI on Railway; frontend Next.js/TS on Vercel at
namson.dev/bet-suggester — now a **four-league platform shell** (WC26/MLS/EPL/La Liga)
with the World Cup dressed in champions' gold. The tournament is complete; the code is
UNFROZEN; deploys are routine because the DB wipe is now **fully self-healing** —
results, closings, bracket AND the settled bot ledger all rebuild at boot from feeds
and committed artifacts (the manual restore procedure, proven ×12, is the fallback).

### How the tournament ended
- **THIRD, Jul 18 (Hard Rock, Miami): England 6–4 France, FT.** A ten-goal carnival:
  Rice 3', Konsa 18', **Saka 37'/46'/87'(pen)**, Bellingham 90+8 vs Mbappé 48'/66',
  Barcola 54', Dembélé 90+6. Model's anchored read: home/draw/away 41.2/22.6/36.2 —
  the away leg landed. Mbappé's brace sealed the **Golden Boot at 10 goals**.
- **FINAL, Jul 19 (MetLife): Spain 1–0 Argentina, AET.** Ferran Torres, 106th-minute
  volley; Argentina red card in extra time. Frozen model: **Spain 53.9 / Argentina 46.1**
  to be champion; win90 40.6/25.8/33.6; sweet-spot cluster **1-1/1-0/0-1/0-0 ≈ 38%**.
  Ninety minutes ended **0-0** — inside the cluster the V5 dispersion change created —
  and the trophy score 1-0 was the cluster's second book. The model's week.
- **Spain's tournament, corrected record** (Son caught the error): **ONE goal conceded
  in eight matches** (De Ketelaere's quarterfinal counter — nothing else, all month),
  0.29 opponent-adjusted xGA/game, unbeaten: a goalless opening draw with Cabo Verde,
  then seven straight wins. M104 was the dataset's most complete suffocation: 2.52 xG
  created, 0.07 conceded, across 120 minutes.

### Where things stand right now
- **Repos:** backend `~/dev/wc26-bet-suggester` @ `1e72c43` (**296 tests green**, 93
  commits, ~8.7k LOC src + ~4.2k LOC tests); frontend `~/dev/namson-dev` @ `1343e59`
  (101 commits, ~6.3k LOC). Both pushed; local == remote == deployed.
- **Prod:** both post-final stat deploys landed Jul 21; **both wipes restored losslessly**
  (84/84 positions, leaderboard exact); boot self-heal verified post-tournament — a fresh
  container rebuilds ALL results + the full bracket (champion: Spain) from ESPN's dated
  scoreboards in ~2 minutes.
- **Final leaderboard (settled, archived, restorable):** KELLY **+454.73** first …
  SHEEP **−119.25** last. Full table + verdict in Part D.
- **Research archive:** COMPLETE — all 16 knockout matches' lock/closing/result triples
  (incl. the KXMENWORLDCUP champion books), every signal batch, the tracker's final
  state, six ledger restore sources. Part E.
- **Alerts:** Discord two-channel (action TL;DR + detail w/ narrator briefs) + ntfy.sh
  phone push, all server-side, Mac-independent, provable via `POST /api/alerts/test`
  (operator-token required in read-only mode). The committed ntfy default was REMOVED
  Jul 21 — pushes no-op until Son sets a fresh `NTFY_TOPIC` env var (Part G).
- **Stats:** all four semifinalists folded through their full 8-match tournaments
  (M99–M104 PMSRs): Spain atk 1.45 def **0.65**, Argentina 1.45/**0.83**, England
  1.45/**0.95**, France 1.45/**0.86**; all four scouting blurbs rewritten from what
  actually happened.
- **Model evaluation COMPLETE (Part I), claims revised per the Jul 21
  independent evaluation:** 11/14 knockout winner calls (one-sided p=0.029,
  two-sided p=0.057 — suggestive, not proven); Brier 0.0898 vs 0.0911 over 293
  frozen markets = **parity with the executable ask** (cluster CIs straddle
  zero); ECE lower under the primary binning but **specification-sensitive**;
  AUC identical. The evaluation found four real defects — all FIXED
  same-day (xg_model v2 centered set-piece, per-draw first-goal mixture,
  all-in-cost Kelly, bid-side exit valuation) — and the public API now FAILS
  CLOSED to read-only, with authenticated operator controls awaiting only
  Son's ADMIN_TOKEN + fresh NTFY_TOPIC env batch (Part G).

### The weekend in one list (what V6 added over V5)
1. **Five new arena bots** (`95884f1`) — the V5-proposed controls, ALL built: 🪙 COIN
   (seeded random placebo), 🐑 SHEEP (model-blind price-follower — the anti-model
   control), 🎯 SNIPER (KELLY's rule, last-15-min window only), 😤 TILT (martingale
   favourite backer) — plus a new commission: 📚 **SCHOLAR, the learner**, who copies
   peer-weighted consensus and refuses families his mentors have lost money in. Exact
   rules in Part D. Twelve bots total.
2. **Position tracker** (`21d879d`, backlog H1 → SHIPPED) — Son's REAL holds priced
   every live tick: hold-to-settlement EV vs cash-out-now (both fee-aware),
   EXIT/HOLD/CLOSE_CALL verdicts, alert on verdict flips (±5pt margin,
   `POSITION_FLIP_MARGIN`). `/api/positions` GET/POST/DELETE. Proved in anger during
   the final with Son's 7-position, $599 slip (V6.1 addendum).
3. **Alert fan-out + narrator** (`21d879d`, `361f988`, `e925624`) — `send_alert` fans to
   Discord **action** channel (TL;DR, quick decisions), Discord **detail** channel
   (full context + the NARRATOR: template-built live match briefs every few minutes),
   and **ntfy.sh** phone push (instant, no app open, Mac-independent).
   `POST /api/alerts/test` proves all channels BEFORE a match, not during.
4. **Lossless deploys** (`2cf2ce2`) — `POST /api/bots/restore` (idempotent per
   bot+market_id) + the export-first procedure killed the ephemerality terror: proven
   across TWELVE wipes including two on final-stats day and four during the
   post-final documentation pushes, each restored and verified hands-off in ~20s. The reprime ritual is DEAD —
   Son deleted the `MIN_CONFIDENCE` env var; settings boot 0.45 natively.
5. **Championship-series classification** (`7274fb6`, `6409b20`) — KXMENWORLDCUP books
   ("Will Spain win the World Cup?") now key as advance on the FINAL only, denied
   elsewhere; family added to the research capture so champion positions settle. The
   "advance vs win-in-90" confusion class is now regression-tested from three angles.
6. **Two settle-path bugs found by real settlements** (`2cf2ce2`, `58ed49d`) — the
   last-price heuristic read a key Kalshi doesn't send (`last_price` vs
   `last_price_dollars`), and `bankroll()` was silently REFUNDING the cost of every
   closed position. Both invisible until the first live prod settlements exposed them.
7. **A prod outage found and fixed mid-weekend** (`18d54d2`) — bots_tick crashed every
   run for ~15 min: SQLite round-trips DateTime NAIVE and `_price_trends` compared
   naive vs aware in Python. Lesson burned in: this codebase compares datetimes in SQL
   WHERE clauses; if you must compare in Python, normalize tzinfo. Regression-tested.
8. **V5-audit fixes shipped** (`01ac89d`) — ET/pens continuation now runs WITH gamma
   dispersion (H5#5), easy-win sweep dedupes per outcome_key with cooldown keyed
   match+outcome (H4#4), `/api/bots` equity is mark-to-market from the newest
   OddsReading (H5#3).
9. **Stats through the full tournament** (`dd2e32c`, `3a39559`, `1e72c43`) — France +
   England folded through the SFs pre-THIRD; then all four semifinalists re-folded
   through M103/M104 with blurbs rewritten from the actual matches; then Spain's
   conceded count corrected 2→1 after Son caught an inherited error the data disproved
   (the fold arithmetic was right; the prose wasn't).
10. **The four-league frontend platform** (~40 commits, `61b12a9`→`1343e59`) — the
    dashboard hero is now a drive-mode league switcher (WC26/MLS/EPL/La Liga): arrows,
    swipe, per-league theme colors + wordmark-adjacent typefaces + official logos, and
    BESPOKE full-page reveal transitions per league. The World Cup got the champions'
    treatment: trophy-gold theme, official emblem watermark, a "golden moment"
    transition raining Rojigualda confetti/flags/crests/fan-chants, a permanent
    ★★ CAMPEONES·ESPAÑA badge — and the bracket pyramid FLIPPED, champion at the
    summit. Full detail + the hard-won animation laws in Part A.
11. **`~/dev/competitions/` umbrella** — four seeded league workspaces with
    engine-adaptation checklists (fixtures source, ticker verification, xG pipeline,
    league-play deltas, fresh ledgers). MLS is in-season now; EPL/La Liga start
    mid-August. The generalization scaffold, ready when Son is.

---

## PART A — ARCHITECTURE (V6 full map)

### Backend `~/dev/wc26-bet-suggester` (Python 3.12, FastAPI, SQLAlchemy/SQLite, APScheduler)
```
config.py                 every knob, env-overridable — READ THE COMMENTS, they carry the
                          honest contracts. V6 additions: POSITION_FLIP_MARGIN (0.05),
                          NTFY_TOPIC, DISCORD_ACTION/DETAIL_WEBHOOK_URL,
                          NARRATOR_INTERVAL_MINUTES
api/main.py               ~37 endpoints; V6 adds /api/positions (GET/POST/DELETE),
                          /api/bots/restore (idempotent ledger restore),
                          /api/alerts/test (prove channels); /api/bots equity is now
                          mark-to-market from the latest OddsReading
src/schedule_data.py      fixtures + hand-curated team stats (opponent-adjusted xG formula
                          in docstring) + scouting blurbs (rewritten post-final from the
                          actual matches) + effective_team_stats (ET fatigue)
src/models/simulator.py   Monte Carlo: Poisson goals + KNOCKOUT_DAMPING 0.85 + gamma
                          dispersion (_dispersed — NOW INCLUDING the ET/pens continuation
                          path) + red-card coefficients + halves + first-goal race +
                          simulate_remaining
src/suggester.py          SuggesterEngine: run_for_match (persist + anchor 60/40 with
                          market), price_live (live repricing + model-first completeness
                          rows; defence mults)
src/live_feed.py          ESPN keyless backbone (scoreboards bucket by US-EASTERN date
                          ±1!); API-Football free plan is SEASON-BLIND for 2026
src/live_state.py         15s live tick; freeze on FT; restore_missing_results (FIXPOINT
                          self-heal — verified POST-tournament: full bracket incl.
                          champion rebuilt from dated ESPN in ~2 min on a fresh container)
src/live_auto.py          the self-running live read: suggest_levers (share + openness +
                          momentum), sim_minutes clamps, live_auto cycle
src/live_plays.py         commentary parser (typed weighted threat events) + momentum
                          (12-min window, 6-min half-life, ±12% tilt cap)
src/live_signals.py       watched BUY/SELL (±8pts) + easy-win sweep (≥85% model, ≤90¢,
                          ≥5pt gap); dedupe per outcome_key; cooldown keyed match+outcome
src/bots.py               THE TWELVE-BOT ARENA (personas, entry rules, fee model,
                          settlement, martingale streaks, scholar weights, seeded RNG,
                          tz-normalized price trends) — all rules exact in Part D
src/positions.py          NEW: the position tracker — TrackedPosition rows, _verdict
                          (hold_ev = contracts·p vs cashout = contracts·(price−fee)),
                          evaluate_positions on the live tick, _maybe_alert on flips
src/alerts.py             NEW SHAPE: send_discord(msg, channel=action|detail) +
                          send_ntfy + send_alert(msg, kind) fan-out; all no-op safely
                          when unconfigured, never raise
src/narrator.py           NEW: template-based live match briefs on the detail channel
                          (score, momentum, model vs market, signal context)
src/research.py           capture_closing_snapshot — NINE market families now
                          (+ KXMENWORLDCUP so champion books have closing rows)
src/bracket.py            resolve_bracket from frozen results, forecasts (seeded
                          lru_cache — the champion-jitter fix predates V6)
src/reference_odds.py     fallback chain: API-Football → DraftKings-via-ESPN → Kambi CDN
src/player_props.py       anytime/first scorer from player_rates.json (PMSR-derived)
src/timing.py             ripeness scoring for watched markets (pre-match)
jobs/scheduler.py         hourly predictions · minute final-lock · odds poll · 15s live
                          tick · 30s signals · 60s bots · 30min bracket · boot chain
                          restore→resolve→prime
scripts/fifa_extract.py   FIFA Training Centre PMSR pipeline (manifest-gated tripwire);
                          manifest now carries ALL 47 matches, expected_per_team 8
scripts/build_player_rates.py  rates from extracted PMSRs (corpus: ~/dev/wc26-match-pdfs)
research_archive/         committed JSON: the durable research data — COMPLETE (Part E)
tests/                    296 tests. NEVER pipe pytest before a gated push (exit-code
                          masking). V6 additions: test_positions, test_narrator,
                          TestChampionshipSeries, TestSettleAndRestore,
                          TestNewBotEntryRules, naive-datetime regression; the two
                          time-bomb tests (test_upcoming, test_even_match) defused so the
                          suite stays green FOREVER, not just during a tournament
```

### Frontend `~/dev/namson-dev` (Next.js pages router, TS, Tailwind v4; Vercel)
```
src/pages/bet-suggester/index.tsx   THE LEAGUE PLATFORM: LEAGUES config (4 leagues ×
                                    {accent/dim/faint/ambient, logo, glyph treatment,
                                    font, tracking, modeMs}), goLeague (instant swap +
                                    mode-reveal-* + FX overlay), LeagueFX (full-viewport
                                    transition effects, rendered OUTSIDE .mode-stage),
                                    load-time intro (plays the current league's
                                    transition on mount), swipe handlers, champ-badge
                                    (★★ CAMPEONES · ESPAÑA), WC dashboard PERMANENTLY
                                    MOUNTED (hidden via CSS when off-league — unmounting
                                    it jammed the main thread on switch)
src/pages/bet-suggester/market/[matchId].tsx  (~2000 lines) hero/countdown/freshness,
                                    Model Prediction panel, StrategySection (LOW/MED/
                                    HIGH + DIY BigInt atoms, Kelly ternary search,
                                    fee-aware), grouped Markets table (review-mode
                                    after settle), Watch buttons
src/pages/bet-suggester/bots.tsx    Bot Arena: twelve bots, dark canvas, holdings
                                    grouped per match, entry-time order, collapsible
src/components/BracketView.tsx      FLIPPED PYRAMID: Champion → Final (+3rd-place
                                    beside) → SFs → QFs → R16; ChampionBox's legacy
                                    connector stem above CHAMPION removed
src/components/LiveScoreboard.tsx   live cards + LiveExtras (auto stream, stats, news,
                                    signals) + LiveMarketStream
src/styles/globals.css              :root accent = TROPHY GOLD #f5c542 (WC26 is the
                                    default league — match pages + Bot Arena follow);
                                    all league transition keyframes; league-glyph
                                    clamp(190px,52vw,340px); prefers-reduced-motion
                                    kills every effect
public/leagues/                     wc26-official.png (official emblem, TRUE-BLACK 26,
                                    FIFA wordmark inked out with sampled #000),
                                    wc26-emblem.png (gilded fallback), wc26-trophy.png,
                                    mls.svg, epl.png (lion-only, keyed),
                                    laliga.png (monotone, 3× supersampled rebuild)
```

### The league transitions (bespoke, per Son's spec) + the animation laws
- **WC26 — "the golden moment":** trophy-gold bloom + sunburst from the emblem's
  measured point, then a 3.2s Spanish rain — Rojigualda confetti, streamers, mini
  flags, SVG escudos, and falling fan-chants (¡CAMPEONES! / ¡VIVA ESPAÑA! / OÉ OÉ OÉ /
  ¡A POR ELLOS!).
- **MLS:** black curtain slash-wipe — transform-only curtain with the slash painted on
  its edge (clip-path animation was the lag).
- **EPL:** liquid-glass droplets expanding over the page, backdrop-filter melting out
  in-keyframe; droplets born invisible mid-scale (the "spawn dot" fix).
- **La Liga:** a pre-painted 230vmax conic disc ROTATING VIA TRANSFORM, pivoted on the
  logo's measured center; fade lives inside the keyframes so the arm dissolves with its
  spin.
- **THE LAWS (hard-won, in blood):** compositor-only — never animate left/clip-path/
  gradient-angles/masks on large surfaces; transform+opacity, will-change hints; the
  effect window must OUTLIVE the slowest child animation; position:fixed breaks inside
  transformed ancestors (FX renders outside the stage); static masks are fine
  (rasterized once); JSX text does NOT resolve \uXXXX escapes — use real UTF-8;
  Lightning CSS minifies blur(0px)→blur() (valid, don't chase it); cache-bust image
  assets by RENAMING; verify PIL-keyed assets by compositing a preview on the dark
  canvas at page opacity BEFORE shipping.

### Deployment & ops
- **Railway** (backend): auto-deploys on push to MAIN; DB is EPHEMERAL — but the wipe is
  now **LOSSLESS BY PROCEDURE** (Part F). Builds can queue 10 min to 3+ hours; watch
  and restore in the background. Non-main branches do NOT deploy.
- **Settings boot 0.45 natively** — `MIN_CONFIDENCE` env var deleted Jul 19; the
  post-deploy reprime ritual is dead. Discord webhooks + NTFY_TOPIC live as env vars.
- **Vercel** (frontend): auto-deploys on push; no DB anywhere near it.
- **Prod URL:** `https://wc26-bet-suggester-production.up.railway.app`; frontend
  `https://namson.dev/bet-suggester`.
- **Memory files** (auto-loaded per session): match-day-briefing (now the tournament
  chronicle), competitions-structure, railway-db-ephemeral, archive-before-deploy,
  gated-push, api-football limits, prod URL, remote-control workaround, desktop-sync
  incident.

---

## PART B — THE MODEL (exact, as deployed — FINAL EDITION)

1. **Team stats** (`schedule_data.py`, hand-curated from FIFA PMSR data):
   `attack = clamp(xGF_adj/1.30, 0.75, 1.45)`; `defence = clamp(0.55 + 0.45·(xGA_adj/1.30),
   0.62, 1.06)` (higher defence = leakier); `xGF_adj = xGF·(oppElo/1650)`,
   `xGA_adj = xGA·(1650/oppElo)`, averaged per game over the team's full tournament.
   Folds are incremental — `new_avg = (7·old_avg + game8_adj)/8` — and the incremental
   method was VALIDATED twice by full recomputation (≡ to 4 decimals).
   **Final tournament stats (all 8 matches folded):** Spain atk 1.45 def **0.65**
   (0.29 adj xGA/g — the best defensive number in the dataset); Argentina 1.45/**0.83**
   (attack avg 1.9001, comfortably above the 1.885 cap threshold); England
   1.45/**0.95**; France 1.45/**0.86**.
2. **Simulation** (`simulator.py`, N=10,000): xG from attack×opp-defence (+form/fatigue/
   set-piece modifiers); knockout λ ×0.85; per-sim gamma multipliers CV=0.30 — now
   applied on EVERY path including ET/pens continuation; red cards sampled; ET = 30
   more minutes of the same process; pens ≈ coin flip w/ slight home edge; halves,
   first-goal race, scoreline distribution, advance probabilities.
3. **Market anchoring:** final probability = 0.60·model + 0.40·implied. Kalshi fee
   0.07·P·(1−P); netOdds = 1/(P+fee). Board is likelihood-first.
4. **ET fatigue rule** (`effective_team_stats`): a team whose LAST match went AET gets
   a fatigue bump automatically — hand stats stay clean.
5. **Live remainder** (`price_live`): score-seeded, time-scaled, red-card aware, attack
   levers × openness levers × momentum tilt; model-first completeness rows where Kalshi
   closed the book.
6. **The scoreboard, final:** freeze call Spain 53.9% champion ✓; the 90-minute 0-0 sat
   inside the frozen sweet-spot cluster (the V5 dispersion change put it there); THIRD's
   away-England leg (36.2) landed. The full Brier/calibration accounting is DONE —
   every table in Part I, reproducible via `scripts/score_calibration.py`.

## PART C — LIVE PIPELINE (cadences — unchanged core, new outputs)
```
15s   live_tick → poll_live_state (ESPN keyless fall-through) → snapshot upsert;
      freeze on FT → capture_closing_snapshot (9 families) + resolve_bracket
      → ALSO: evaluate_positions (tracker verdicts, alert on EXIT/HOLD flips)
~25s  live_auto out-cache: stats + commentary → parse_plays → suggest_levers → price_live
30s   live_signals job: watched BUY/SELL ±8pts; easy-win ≥85%/≤90¢/≥5pt;
      dedupe per outcome_key; cooldown keyed (match, outcome)
60s   bots tick: entries/exits/settlements for all twelve
~min  narrator: live brief to the Discord detail channel (NARRATOR_INTERVAL_MINUTES)
30s   frontend polls: auto stream, stats, signals
```
ESPN quirks (hard-won, still true): scoreboards bucket by US-EASTERN date (try kickoff
±1 day); orient teams BY NAME never homeAway; never cache empty answers; commentary
time.value is seconds; **NEW (found during the final): ESPN reports the ET interval with
status "HT"** — live_auto clamps sim to minute 45 during extra-time breaks (phase-aware
clamp is on the backlog, Part G).

## PART D — THE BOT ARENA (twelve, all rules exact) + THE VERDICT
$1,000 each; fee 0.07·P·(1−P) on entry AND early exit; one position per bot per market;
settlement from MarketClosing result (yes/no), else last-price heuristic
(`last_price_dollars` fallback chain — the V6 fix), else wait. Equity mark-to-market.

**The original seven (V5, unchanged):**
- 🤓 **KELLY** pre-match; edge ≥5pts, price ∈[10¢,90¢]; stake = cash·f*/2 capped $150.
- 😌 **CHALK** pre-match; model ≥65% and price ≤85¢; flat $50.
- 🎰 **MOONSHOT** pre-match; price ∈[2¢,20¢] and model/implied ≥1.4; flat $10.
- ⚡ **WIRE** in-play; $40 on fresh BUY/easy-win signals; exits on SELL or +20¢
  take-profit.
- 🧊 **FADE** in-play; price crashed ≥15¢ from the T-10 lock AND live model ≥ price+8pts;
  $60; holds to settlement.
- 🍯 **SWEETSPOT** pre-match; modal exact score + neighbours ≥60% of mode (max 4);
  $60 dutched ∝ model p.
- 🤝 **CREW v3** Son's crew recipe (even game → ladder; mismatch → strong-side scores;
  knockout 1-1 always; 0-0 when ≥6%; 2-2 when draw ≥25%); $60 ∝ model p.

**The V6 five (all built `95884f1`):**
- 🪙 **COIN** — the placebo. 3 books per match, seeded RNG (deterministic per match),
  $20 each. Exists so every other bot has a luck baseline.
- 🐑 **SHEEP** — the anti-model control. Model-BLIND: buys whatever the market itself
  bid up ≥4¢ over the OddsReading trend window; top 3 risers, $40 each. If SHEEP beats
  KELLY, the edge thesis is in trouble.
- 🎯 **SNIPER** — KELLY's exact rule, but the window only opens 15 minutes before
  kickoff. Tests bet-early vs bet-late — the question the ripeness system was built on.
- 😤 **TILT** — the cautionary ledger. One bet per match on the market favourite
  (to win, no draws), martingale-staked: $10 base doubling per trailing settled loss,
  capped $160.
- 📚 **SCHOLAR** — the learner. Copies a market when the PEER-WEIGHTED count of bots
  holding it reaches 3.0 (weight = 1 + realized_net/$50 for profitable mentors), dutches
  $60/match ∝ support, and REFUSES any market family where the fleet's settled aggregate
  is ≤ −$15 (the "learned mistake" half). Named mentor in the note when one bot's weight
  led the copy.

**FINAL LEADERBOARD (settled, 84 positions, archived + restorable):**
```
 1. 🤓 KELLY      +454.73   (5 trades, 4-1)   ← model edge, half-Kelly discipline
 2. 🎯 SNIPER     +282.48   (2 trades, 2-0)   ← only ever fired on THIRD
 3. 🧊 FADE       +104.28   (3 trades, 1-2)
 4. 📚 SCHOLAR     +66.71   (9 trades, 1-8)   ← KELLY-led copies carried it
 5. 🎰 MOONSHOT    +15.13   (7 trades, 1-6)
 6. ⚡ WIRE          +3.78  (10 trades, 8-2)   ← scalps real; late state-holds ate them
 7. 🍯 SWEETSPOT    −9.50   (8 trades, 1-7)
 8. 🪙 COIN        −31.93  (16 trades, 3-13)  ← gave Friday's variance back. Working placebo.
 9. 😌 CHALK       −49.31   (1 trade, 0-1)
10. 😤 TILT        −52.82   (4 trades, 1-3)
11. 🤝 CREW        −58.29  (16 trades, 1-15)  ← out-of-sample verdict on the crew recipe
12. 🐑 SHEEP      −119.25   (3 trades, 0-3)   ← DEAD LAST
```
**THE VERDICT (revised Jul 21 — pilot-strategy-result, not proof):** in a
**two-match paper pilot** (the settled ledger spans only THIRD + FINAL; earlier
bot history died in pre-procedure wipes), model-driven staking finished first and
the model-blind follower last — but the ordering is dominated by ONE match
direction expressed through correlated contracts: KELLY's +454.73 is four
overlapping England positions in THIRD plus a FINAL loss; SNIPER's and SHEEP's
results are the same England-vs-France call from opposite sides. Add ask-side
fill optimism (Part G) and the leaderboard is an anecdote with good
instrumentation, not evidence of a general strategy ranking. CREW's 1-15 remains
the honest out-of-sample answer to its in-sample +47.4% backtest. The live KELLY
rule's own flat-staked replay across all six lock matches is NEGATIVE (−11.2%,
Part I.4) — its bankroll result came from stake sizing and window, and saying so
plainly is worth more than the headline was.

## PART E — RESEARCH SYSTEM (archive COMPLETE)
- **T-10 lock**: `is_final` Prediction batch at kickoff−10min (the unfudgeable model
  view). **Locks are the ONE thing a wipe still destroys** — which is why every lock is
  archived the moment it exists.
- **Closing snapshot**: every priced family (NINE, incl. KXMENWORLDCUP) captured at
  freeze; idempotent; backfillable (Kalshi keeps settled markets queryable).
- **`/api/research/{id}`** = {final_lock, closing, last_readings, result}.
- **`research_archive/` — the finished corpus** (committed, deploy-proof):
  - Full lock/closing/result triples for ALL 16 knockout matches — R16 through THIRD
    and FINAL, including the champion books.
  - `live_signals_*` — every signal batch: SF1/SF2 (7/10 record), THIRD (7 signals),
    FINAL (5 signals incl. the champion-book watched SELL).
  - `tracker_final_state_*` — the position tracker's last word on Son's real slip
    (the champion hold reading EXIT).
  - `bots_ledger_restore_source*` ×6 — timestamped full-ledger exports; source6
    (2026-07-21, 84 positions) is the canonical final state.
  - `watchlist_*`, prediction timelines, pre/post-THIRD ledger snapshots.
- **What this enables (the post-final harvest, Part H):** Brier scorecard of locked
  model vs closing line vs result across 16 matches × 9 families; calibration
  write-up; signal-grading; sweet-spot cluster width tuning with real payoffs.

## PART F — OPS RUNBOOK (post-tournament edition)
The match-day loop is retired (kept in V5 for history). What remains is DEPLOY OPS:

**The lossless deploy procedure — now AUTOMATIC:** since the Jul 21 evening
patch the boot chain restores the settled ledger from the committed archive
(`restore_ledger` step, `src/bots.py::restore_from_archive`) alongside results
and closings — a deploy wipe heals ALL state with no operator action. The
manual procedure below remains as the fallback (now requires the
`X-Admin-Token` header in read-only mode):

**(manual fallback, proven ×12 while it was the primary):**
1. `curl /api/bots > research_archive/bots_ledger_restore_sourceN_<UTC>.json` — export
   FIRST, commit it. Also archive any new research bundles (`/api/research/{id}`).
2. Push to main. Railway builds (10 min to 3+ hours — WATCH IN THE BACKGROUND, never
   foreground-wait).
3. Detect the wipe (positions → 0), then `POST /api/bots/restore` with the export.
   Idempotent per bot+market_id — safe to fire twice, safe if a second build lands.
4. Verify: 84/84 positions, leaderboard exact (KELLY +454.73 … SHEEP −119.25),
   settings 0.45, `/api/bracket` champion=Spain, team-info blurbs current.
   Boot self-heal handles results/closings/bracket on its own (~2 min).

**Gated pushes:** `python -m pytest -q >/tmp/pyout 2>&1; RC=$?; tail -1 /tmp/pyout;
[ $RC -eq 0 ] || exit 1` — NEVER pipe pytest (exit-code masking). Frontend pushes are
free (Vercel, no DB).

**Remote Control:** desktop-app RC toggle is broken for this session lineage — use
`claude --resume` + `/remote-control` from a standalone Terminal (may take 2 tries).
Server-side alerts (Discord/ntfy) are Mac-independent and cover the gap.

## PART G — KNOWN DEBT (V6 — what remains, honestly)
**Resolved since V5** (for the record): position tracker ✅ built · control bots ✅ all
four + SCHOLAR · MIN_CONFIDENCE env ✅ deleted (reprime dead) · ET-continuation
dispersion ✅ · easy-win dedupe ✅ · mark-to-market equity ✅ · champion-forecast jitter ✅
(seeded cache) · Discord ✅ two channels + ntfy · settle-path bugs ✅ · naive-datetime
crash ✅ · KXMENWORLDCUP classification + capture ✅ · time-bomb tests ✅ defused.

**Jul 21 evening — the independent-evaluation fixes (same-day, two rounds):**
Round 1: three real model defects corrected with regression tests (set-piece
double counting → xg_model v2 centered adjustment; first-goal probabilities
computed per latent-rate draw; Kelly gate + fraction at all-in cost), the
committed ntfy default REMOVED (fail-closed), the public lockdown built, and
the statistics battery moved into `scripts/score_calibration.py` with pinned
outputs. Round 2 (the evaluator's four refinements): sell-side execution FIXED
(bid-side exits/cash-outs/marks, NO_BID = not executable); read-only now FAILS
CLOSED by default with `secrets.compare_digest`, Bearer support, auth-before-
rate-bucket, and the one mutating GET (`force_refresh`) operator-gated; the
LEDGER JOINED THE BOOT SELF-HEAL (committed canonical archive — wipes now heal
bot state with zero API calls); set-piece centering honestly re-scoped
("mitigates, does not fully eliminate" — the extracted corpus has set-play
counts, not set-piece xG); pinned statistics reframed as historical-artifact
assertions (input_version wc26-final-2026-07-21). `scripts/verify_lockdown.sh`
sweeps every OpenAPI mutation mechanically. Suite: 327 green.

**Still owed:**
1. **Set the operator credentials + new alert channel (Son, ONE Railway
   window):** read-only now FAILS CLOSED (default true — nothing to set), so
   the batch is: `ADMIN_TOKEN=<generated secret>` (generate with
   `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`, store a
   copy at `~/.wc26_admin_token` chmod 600 — operator scripts read it),
   a fresh `NTFY_TOPIC` (then re-subscribe in the ntfy app), and optionally
   the two Discord split webhooks. Each save redeploys/wipes — batch in one
   window. The ledger now SELF-HEALS at boot (committed archive), so no
   manual restore is required; run `scripts/verify_lockdown.sh` after.
2. **Regenerate the API-Football key** — pasted in chat ~Jul 5 (never committed; repo
   scanned clean). Son's dashboard task.
3. **Railway volume at /app/data** — still THE structural fix; the lossless procedure
   is a proven workaround, not an excuse. (T-10 locks remain the one unrestorable
   casualty class — mitigated by archiving, solved only by the volume.)
4. **Public repo has no README** — the engineering is A−, the presentation is D, and
   the tournament story is now COMPLETE and telling. Highest-leverage portfolio gap.
   (A portfolio report now exists at `docs/V6/PROJECT_REPORT.md` — distill it.)
5. **ESPN "HT" during ET intervals** — live_auto clamps sim to minute 45 at the ET
   break; needs a phase-aware clamp (kickoff-age or score-state disambiguation).
6. **COIN/TILT/SCHOLAR per-match pins drift** — their "one action per match" markers
   are in-memory; a restart mid-match could double-enter. Bounded, but pin to DB.
7. **Sell-at-bid realism — ✅ FIXED (Jul 21 evening):** exits, take-profits,
   tracker cash-outs, and equity marks all use the YES BID (`_market_yes_bid`,
   persisted per OddsReading); an absent bid means NOT EXECUTABLE (NO_BID
   verdict / held position) — the ask is never silently substituted. The
   SETTLED 2026 leaderboard predates the fix: its early exits (WIRE's scalps)
   remain ask-side optimistic and are labeled a pilot result anyway.
8. **Browser-pane can't hydrate namson.dev** (automation quirk) — DOM reads fine,
   client-rendered panels (bracket, arena) verify by code + build, not screenshot.
9. **macOS update STILL uninstalled** (Son's machine) — it bracketed the final and
   lost; install it at leisure now.

## PART H — FUTURE: THE POST-TOURNAMENT AGENDA
### H1. The harvest (data's ready, commission at will)
- **Brier/calibration write-up — ✅ DONE (Jul 21, `docs/V6/CALIBRATION.md`):** 293
  locked markets scored against backfilled settlement truth. Raw model Brier 0.0898
  vs market 0.0911 (model ahead 7/9 families); the 60/40 blend best at 0.0896 —
  which ANSWERS the MODEL_WEIGHT ratchet question: don't ratchet, the blend won.
  Reproduce: `scripts/score_calibration.py`.
- **Public README + case study** — the repo tells a complete story now: built during
  the tournament, called the champion, ran twelve bots in public, survived its own
  infrastructure. Write it down.
- **V6 → next league** — `~/dev/competitions/` checklists: MLS first (in-season),
  EPL/La Liga mid-August. The engine generalizes: fixtures source + ticker families +
  xG pipeline (no PMSRs in club play — pick a provider) + league-play model deltas
  (no knockout damping, home advantage, squad rotation) + fresh ledgers.
- **Evaluation P1/P2 roadmap (Jul 21 independent evaluation, adopted as the
  next-league gate):** fee-inclusive EV in the suggester's gate; full bid/ask/
  depth lock schema (forecast benchmark ≠ execution comparison); prediction
  `run_id` provenance + DB-level idempotency (final locks, bot pins); deterministic
  sim seeds per (match, model version, data version); dependency lockfile + CI
  pipeline; frontend tests (scenario math first); structured logging; retention
  policy; `_classify_outcome`/match-page decomposition; frozen bot definitions +
  cluster-level reporting for the next arena season.
- Score-effects/dominance dynamics (leads snowball) — still the strongest unimplemented
  idea; still needs more than one tournament of data.
- Minute-aware easy-win thresholds (the late state-hold killer — WIRE's and Son's
  shared failure mode); defensive-effectiveness momentum axis; sweet-spot width tuning
  (now unblocked by the archive); MODEL_WEIGHT ratchet (gated on the Brier work).
- Dixon–Coles: REJECTED Jul 16, stays rejected (documented so nobody re-litigates).
### H3. Sleeping features (one word revives)
- Saved DIY builds; live mark-to-market of strategy-tab dutches; half-Kelly chip;
  manual-panel first-scorer settlement; crew-mode re-sync (v3 vs the crew's actual
  habits — CREW's 1-15 argues for a rethink, not a re-sync).

---

## PART I — THE MODEL'S REPORT CARD (calibration + scorecard, complete)
The evaluation the whole research system existed to make possible. Full narrative in
`docs/V6/CALIBRATION.md`; everything below is reproducible from committed data
(`scripts/score_calibration.py`, `research_archive/settlements_backfill_2026-07-21.json`,
`research_archive/knockout_recon_2026-07-21.json`).

### I.1 Market-level: 293 frozen markets, three probability streams
Six lock-bearing matches (both late QFs, both SFs, THIRD, FINAL). The stored lock
probability is the 60/40 anchored blend; raw = (anchored − 0.4·implied)/0.6 (exact —
MODEL_WEIGHT never changed). Truth = Kalshi settlement, backfilled to 100% coverage.
```
stream        Brier    LogLoss   skill vs market
RAW model     0.0898   0.2887    +1.4%
ANCHORED      0.0896   0.2874    +1.7%   <- best; MODEL_WEIGHT ratchet answered: keep 60/40
MARKET        0.0911   0.2898    —
```
Per family (Brier raw | market): model better in **7 of 9** — SCORE .0292|.0294,
MOV .1173|.1187, SPREAD .0773|.0873, FTTS .1712|.1746, GAME .2590|.2747, ADVANCE
.2384|.2502, BTTS .1820|.2386; market better on TOTAL .1601|.1417 (tail games vs
knockout damping — the score-effects backlog item's fingerprint) and the 2-row
champion book. Pooled Brier is exact-score-heavy (155/293 rows); the family view is
the fairer lens. Per match: model won SF1/SF2/THIRD, market won the two QFs + FINAL.

### I.2 Match-level: the knockout scorecard (14 matches, independent samples)
Frozen locks for six; the rest reconstructed by re-simulating with the EXACT commit
deployed at each kickoff (git archaeology, labeled). CAN_MAR + PAR_FRA excluded
honestly: the repo's first commit (Jul 5 02:36 UTC) post-dates both kickoffs — the
model was born mid-R16.
```
BRA_NOR  Brazil 62%      Norway 2-1     MISS   recon
MEX_ENG  England 52%     England 3-2    HIT    recon
POR_ESP  Spain 70%       Spain 1-0      HIT    recon
USA_BEL  Belgium 60%     Belgium 4-1    HIT    recon
ARG_EGY  Argentina 69%   Argentina 3-2  HIT    recon
SUI_COL  Suisse 50.8%    0-0 pens (SUI) HIT    recon   <- called a coin flip a coin flip
MAR_FRA  France 62%      France 2-0     HIT    recon
ESP_BEL  Spain 57%       Spain 2-1      HIT    recon
NOR_ENG  England 55%     England AET    HIT    frozen
ARG_SUI  Argentina 55%   Argentina AET  HIT    frozen
SF1      France 53%      Spain 2-0      MISS   frozen
SF2      Argentina 52%   Argentina 2-1  HIT    frozen  <- the lone model-market disagreement; model right
THIRD    France 53%      England 6-4    MISS   frozen
FINAL    Spain 55%       Spain 1-0 AET  HIT    frozen
```
**11/14 (78.6%).** Advance Brier 0.2097 vs coin-flip 0.2500. Reconstructed era 7/8
(the one miss = Norway over Brazil, the tournament's biggest upset); frozen era 4/6.
Every miss came at ≤62% confidence; the market lost two ~70% France calls.

### I.3 Significance (revised per the Jul 21 independent evaluation)
```
11/14 vs coin flip            ONE-sided p = 0.0287, TWO-sided 0.0574   suggestive; 5% only one-sided
advance Brier vs coin flip    0.2097 vs 0.25; skill CI brushes zero    borderline, RNG-sensitive
raw vs market (293 rows,      +1.4% point, CI [-10.0%, +11.9%],        indistinguishable — parity
  6-match cluster bootstrap)    P(better) = 59%
anchored vs market            +1.7% point, CI [-4.8%, +7.9%], 68%      indistinguishable, leaning model
expected calibration error    raw 0.0269 vs mkt 0.0384 (10-bin width)  BINNING-SENSITIVE: anchored wins
                              ordering flips under equal-count bins;     equal-count specs; diff CI
                              cluster CI for diff [-0.017, +0.033]       straddles zero — NOT established
discrimination (AUC)          0.893 / 0.894 / 0.890                    identical
```
IMPORTANT SEMANTICS: the market stream is the executable ASK (deliberate for
execution math) — every model-vs-market row is an execution comparison, not a
neutral forecast benchmark; asks carry spread, which flatters the model. Full
evidence-hierarchy labels in CALIBRATION.md. Cluster bootstrap resamples matches,
seed 26, 10k draws, emitted by `scripts/score_calibration.py` and pinned by
`tests/test_calibration_pipeline.py`.

### I.4 The trading replays + the verdict
TWO replays, flat $1 at implied + real fees (they are DIFFERENT rules — the
original conflation was an evaluation finding):
- raw edge ≥5pt (descriptive replay, retrospective rule): 28 bets, 13 wins, +3.0%
- anchored edge ≥5pt (THE LIVE KELLY GATE): 17 bets, 6 wins, **−11.2%** — the live
  rule flat-staked across all six lock matches LOST; KELLY's +45% bankroll came
  from stake sizing plus its two-match window, and is a pilot-strategy-result.
Calibration buckets are clean except 40–50% (predicted .458, realized .304, n=23).

**THE VERDICT, one line: probability-precise, not clairvoyant — and honestly
bounded.** Suggestively better than chance (one-sided); statistical parity with a
real-money exchange's executable prices; calibration competitive under every
specification and ahead under the primary one; identical discrimination. The
measurable edge is humility — being right about how close close games are. Do not
claim "beat the market", "proven precision", or "better-calibrated than the
exchange" anywhere: claim PARITY PLUS DISCIPLINE, because that is what survives
every specification and is reproducible from this repo.

---

## V6.1 ADDENDUM — THE WEEKEND CHRONICLE (for the record)
**Son's real slip (the human ledger):** $599 across 7 positions going into the final —
Spain advance 245c @ 59.3¢, Over 2.5 686c @ 42¢, and a 2-0/2-1/3-0/3-1/3-2 ladder.
The pre-match joint analysis mapped the hole precisely: a "quiet Spain triumph" world
(low-scoring Spain win) loses ~$354 while the thesis is RIGHT. Son held back three
ladder rungs on that analysis (saved ~$77). The final was the quiet-triumph world
almost exactly — 0-0 at 90', one goal in 120 — the Over and ladder died, the advance
leg won: net ≈ **−$277**, with the position tracker flashing EXIT on the champion hold
at 89.5¢ against a 70.4% model read minutes before Torres scored. The night validated
the MAP (every scenario landed where it was priced) while costing money — which is
exactly the difference between a model and a bankroll, and both ledgers (bots up top,
this one) now say so in public.

**Bugs the weekend surfaced, in the order reality found them:** naive-datetime crash in
`_price_trends` (15 min of prod downtime, the weekend's only outage) → settle heuristic
reading a key Kalshi doesn't send → `bankroll()` refunding closed costs → KXMENWORLDCUP
books mislabeled win90 (the "advance vs 90-minute" confusion, THIRD time — now
triple-tested) → KXMENWORLDCUP missing from research FAMILIES (champion positions had
no closing row) → ESPN's "HT" during ET intervals → two time-bomb tests that would rot
after the tournament. Every one fixed same-day except the ESPN clamp (backlogged).

**The two wipes of Jul 21** (final-stats deploys): both restored losslessly by the
background pipeline — export → detect → restore → verify, hands-off. The ephemerality
that terrorized V4/V5 is now a procedure, not a threat.

**Jul 21 evening — the independent evaluation.** Son commissioned an external
technical/quantitative/product evaluation of the full project archive. It was
GOOD: it found three real model defects our audits missed (set-piece double
counting, first-goal mixture math, fee-incomplete Kelly), correctly identified
the bot leaderboard as a two-match pilot dominated by correlated England
positions, showed the ECE headline was binning-sensitive, flagged the one-sided
p-value, and called out claim inflation ("proven", "CI-gated", "one script
reproduces all"). Every code defect was fixed same-day with regression tests;
every inflated claim was rewritten in place (this document included); the
lockdown was built. Its central line — "the engineering system is currently
stronger than the evidence for market edge" — is adopted here as the project's
own official position. Evaluation + evidence bundle archived in Son's Downloads;
verification of its claims (all confirmed, none material found false) is in the
session record.

**Doc lineage:** V1–V4 ghosted with the Desktop (Jul 17 sync incident, V5.1); V5
regenerated into the repo (`docs/PROJECT_DOC_V5.md`, branch `docs-v5-handoff`) — still
the best snapshot of the PRE-final system and the freeze-era constraints; **V6 (this
file) is the tournament's closing state and the platform's opening one.** Docs live in
the repo. Nothing project-critical touches the Desktop. Ever again.
