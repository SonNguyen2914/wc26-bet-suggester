# World Cup 2026 Kalshi Bet Suggester — Project Documentation & Roadmap

**V5 — July 17, 2026.** Handoff edition, REGENERATED COPY: the original V5 file (and the
entire `Desktop/Projects/WC26 Predictor` archive, 5,317 files) went dataless when the
Desktop's sync provider was disabled at ~17:16 Jul 17. This copy was rebuilt verbatim from
the authoring session's context and committed INTO THE REPO so no file-sync layer can ever
evict it again. See the V5.1 addendum at the end for the incident and recovery state.
The V4/V3 history that the original appended verbatim is ghosted with the Desktop; its
outline is preserved in the addendum.

---

## ⚡⚡⚡⚡ CURRENT STATE — V5 SNAPSHOT (July 17, 2026)

### The one-paragraph version
Son's personal research tool that prices every Kalshi World Cup 2026 market against a Monte
Carlo match simulator, live-reprices matches in play from real feeds (with play-by-play
momentum), fires buy/sell alerts, runs a seven-bot paper-trading strategy laboratory (two
bots encode Son's own exact-score recipe), and archives locked-model-vs-closing-line data
for post-tournament calibration. Backend Python/FastAPI on Railway; frontend Next.js/TS on
Vercel at namson.dev/bet-suggester. **CODE IS FROZEN until after the final** — every Railway
deploy wipes the ephemeral SQLite DB (bot ledgers, watchlist, signals, T-10 locks).

### Where things stand right now
- **Fixtures left:** THIRD France–England Sat Jul 18 21:00 UTC (Hard Rock, Miami);
  **FINAL Spain–Argentina Sun Jul 19 19:00 UTC** (MetLife). T-10 locks at 20:50 / 18:50.
- **Model on the final:** Spain 53.9 / Argentina 46.1 to be champion; win90 40.6/25.8/33.6;
  xG 1.66–1.52. Sweet-spot cluster: **1-1, 1-0, 0-1, 0-0 ≈ 38%** of simulations.
- **Repos:** backend `~/dev/wc26-bet-suggester` @ `6309980` (268 tests green);
  frontend `~/dev/namson-dev` @ `fb62eee` (+ three Bot Arena display commits → `df23ca7`,
  frontend-only, DB untouched). Both pushed; local == remote.
- **Results:** all 14 knockout results frozen on prod; brackets fully resolved.
- **Bots:** 7 ledgers live, ~34 pre-match positions on THIRD + FINAL; Wire/Fade wake in play.
- **Research archive** (`~/dev/wc26-bet-suggester/research_archive/`, committed): full T-10
  triples for NOR_ENG, ARG_SUI, SF1, SF2 + closings for MAR_FRA/ESP_BEL + the first 10 live
  signals. This is the durable copy; prod's DB copies die on every deploy.

### The week in one list (what V5 added over V4)
1. **Live BUY/SELL signals on watched markets** + **EASY-WIN sweep over every open book**
   (`src/live_signals.py`, `LiveSignal` table, `/api/live-signals`, 30s job, Discord pushes,
   toasts + badges in the live box). Record so far: SF1 swept 5/5; SF2 went 2/5 — three
   signals died to late goals. 7/10 lifetime.
2. **Openness (defence) levers** — total live shot volume vs xG-implied expectation scales
   both teams' conceding rates, capped [0.85, 1.20] (`suggest_levers`).
3. **Play-by-play pattern read** — ESPN commentary parsed into weighted threat events
   (`src/live_plays.py`); decayed 12-minute momentum share tilts attack levers ±12%.
   Validated on the real SF2 feed: at 78', the minute the losing England-advance signal
   fired, the pattern had England at 23% of recent threat. UI: pattern line + threat ticker.
4. **Goal overdispersion** — gamma-mixed Poisson (`GOAL_DISPERSION_CV=0.30`), fattening
   0-0 and blowout tails. Honest contract in config: it does NOT trim 1-0/0-1 (slightly
   raises them); the tournament's one-nil deficit (4 seen vs 7.6 expected) is p≈0.13, noted
   not actionable. This change put 0-0 into the final's sweet-spot cluster, displacing 2-1.
5. **The Bot Arena** — 7 paper bots, $1,000 each, real books, full fee model, automatic
   settlement from closing snapshots. Page `/bet-suggester/bots`. Detail in Part D.
6. **CREW v3** — Son's crew recipe upgraded on evidence (permanent knockout draw insurance,
   0-0 cagey-game read, belief-weighted stakes, parked-bus hedge in mismatches). Backtests:
   v1 −32.6% → v2 −4.7% → v3 +47.4% (in-sample caveat: fixes derived from those 4 matches).
7. **Sweet-spot indication** on every match page's most-likely-score panel (🍯 cluster
   markers + combined coverage; same ≥60%-of-mode rule the Sweetspot bot bets).
8. **Restore fixpoint** — the self-heal now loops restore→resolve-bracket→restore until a
   pass heals nothing; fixed the permanently-unhealable-SF1 bug. Also removed the stale
   `API_FOOTBALL_KEY` gate that silently disabled DB-only bracket resolution.
9. **Boot-race fix** (spawned session, `d4a6fa4`): placeholder slots are never priced or
   persisted; prime jobs chain after restore/resolve.
10. **M99–M102 PMSRs** extracted (45 PDFs, 7 per finalist); England/Argentina/Spain
    opponent-adjusted through their full samples; player rates rebuilt (Messi .39 8g,
    Kane .35/Bellingham .33, Mbappé .41, Oyarzabal .32).

---

## PART A — ARCHITECTURE (V5 full map)

### Backend `~/dev/wc26-bet-suggester` (Python 3.11, FastAPI, SQLAlchemy/SQLite, APScheduler)
```
config.py                 every knob, env-overridable — READ THE COMMENTS, they carry the
                          honest contracts (dispersion, damping, signals, easy-win, budgets)
api/main.py               ~35 endpoints; the review-mode prediction path; bots ledger
src/schedule_data.py      fixtures + hand-curated team stats (opponent-adjusted xG formula
                          in docstring) + scouting blurbs + effective_team_stats (ET fatigue)
src/models/simulator.py   Monte Carlo: Poisson goals + KNOCKOUT_DAMPING 0.85 + gamma
                          dispersion (_dispersed) + red-card coefficients + ET/pens
                          continuation + halves + first-goal race + simulate_remaining
src/suggester.py          SuggesterEngine: run_for_match (persist + anchor 60/40 with
                          market), price_live (live repricing + model-first completeness
                          rows `model:*` where Kalshi closed books; defence mults)
src/live_feed.py          ESPN keyless backbone: scoreboards (US-Eastern date buckets ±1!),
                          live_state_for fall-through, espn_match_stats, espn_lineups,
                          espn_commentary; API-Football (free plan is SEASON-BLIND for
                          2026 — live=all works, everything else errors "try 2022-2024")
src/live_state.py         15s live tick; freeze on FT; restore_missing_results (FIXPOINT);
                          kickoff-age guard against FT-card floods
src/live_auto.py          the self-running live read: suggest_levers (share + openness +
                          momentum), sim_minutes clamps (stoppage floors 44/88/118, HT=45,
                          P=120), live_auto cycle (~25s out-cache, 75s Kalshi book cache)
src/live_plays.py         commentary parser (typed weighted threat events) + momentum
                          (12-min window, 6-min half-life, ±12% tilt cap)
src/live_signals.py       watched BUY/SELL (±8pts) + easy-win sweep (≥85% model, ≤90¢,
                          ≥5pt gap); 180s cooldown, side-flip/strengthen-0.05 refire;
                          persists LiveSignal, pushes Discord
src/bots.py               the 7-bot arena (personas, entry rules, fee model, settlement)
src/research.py           capture_closing_snapshot (idempotent, 8 market families, never
                          raises), closing_rows; /api/research/{id} = lock+closing+result
src/bracket.py            resolve_bracket from frozen results (NO api-key gate), forecasts
src/reference_odds.py     fallback chain: API-Football → DraftKings-via-ESPN → Kambi CDN
src/player_props.py       anytime/first scorer etc. from player_rates.json (PMSR-derived)
src/alerts.py             Discord webhook (no-op without DISCORD_WEBHOOK_URL)
src/timing.py             ripeness scoring for watched markets (pre-match)
jobs/scheduler.py         hourly predictions · minute final-lock · odds poll · 15s live
                          tick · 30s signals · 60s bots · 30min bracket · boot chain
                          restore→resolve→prime
scripts/fifa_extract.py   FIFA Training Centre PMSR pipeline (manifest-gated tripwire —
                          refuses hub PDFs not in scripts/fifa_manifest.json)
scripts/build_player_rates.py  rates from extracted PMSRs; REMAINING = teams w/ matches left
research_archive/         committed JSON: the durable research data (see Part E)
tests/                    268 tests. NEVER pipe pytest before a push (exit-code masking):
                          `python -m pytest -q >/tmp/pyout 2>&1; RC=$?; tail -1 /tmp/pyout;
                          [ $RC -eq 0 ] || exit 1`
```

### Frontend `~/dev/namson-dev` (Next.js pages router, TS, Tailwind; Vercel; ES2020 for BigInt)
```
src/pages/bet-suggester/index.tsx        landing: live box, next-match hero (stage-aware
                                         label), bracket, likelihood-first board
src/pages/bet-suggester/market/[matchId].tsx  (~2000 lines) hero/countdown/freshness,
                                         Model Prediction panel (halves, FT, ET/pens,
                                         🍯 sweet-spot cluster on most-likely scores),
                                         StrategySection (LOW/MED/HIGH + DIY BigInt atoms,
                                         payout ladder, numeric Kelly ternary search,
                                         fee-aware), grouped Markets table (review-mode
                                         after settle: Settled column), Watch buttons
src/pages/bet-suggester/bots.tsx         Bot Arena (cards ranked by net P&L, 60s poll)
src/components/LiveScoreboard.tsx        live cards; LiveExtras (auto stream + stats +
                                         news + signals polls); LiveMarketStream (grouped
                                         rows, model-only "—" columns, levers + pattern
                                         line, threat ticker, 🍯/BUY/SELL badges); team-
                                         colored stat bars (clash-resolved)
src/components/LivePanel.tsx             manual override; attack sliders auto-track live
                                         levers until touched ("● tracking live stats")
src/lib/suggesterApi.ts                  all types + api.*; src/lib/marketGroups.ts
                                         canonical grouping; src/lib/teamColors.ts
src/pages/api/bet-suggester/*            proxies (incl. live-signals, bots)
```

### Deployment & ops
- **Railway** (backend): auto-deploys on push to MAIN. **DB is EPHEMERAL** (SQLite in the
  container, volume never attached). Every deploy wipes: results (self-heal restores),
  closings (self-heal recaptures), T-10 locks (LOST — hence research_archive), watchlist,
  LiveSignal history, bot ledgers. Boot self-heal converges in ~60-90s (fixpoint).
  Non-main branches do NOT deploy (safe for doc/backup pushes).
- **After every deploy:** `POST /api/settings {"min_edge":0.05,"min_confidence":0.45,
  "min_volume":1000}` — a Railway env var `MIN_CONFIDENCE=0.60` still overrides the code
  default and resets on every wipe. (Deleting it is on Son's dashboard list, with the
  volume and regenerating the API-Football key that was pasted in chat early on.)
- **Deploy discipline:** never within ~4h of kickoff or during a match; archive research
  bundles for any settled match with a lock BEFORE pushing (`curl /api/research/{id}` →
  research_archive/, commit). Verify prod after: results=14, bracket resolved, sane
  champion forecast, settings reprimed.
- **Prod URL:** `https://wc26-bet-suggester-production.up.railway.app`; frontend
  `https://namson.dev/bet-suggester`.
- **Memory files** (auto-loaded per session): match-day-briefing (runbook), api-football
  limits, archive-before-deploy, gated-push, railway-ephemeral, prod URL, remote-control
  workaround.

---

## PART B — THE MODEL (exact, as deployed)

1. **Team stats** (`schedule_data.py`, hand-curated from FIFA PMSR data):
   `attack = clamp(xGF_adj/1.30, 0.75, 1.45)`; `defence = clamp(0.55 + 0.45·(xGA_adj/1.30),
   0.62, 1.06)` (higher defence = leakier); `xGF_adj = xGF·(oppElo/1650)`,
   `xGA_adj = xGA·(1650/oppElo)`, averaged per game over the team's full tournament.
   Group-opponent Elos are documented estimates (±40 Elo moves results <0.5%).
   Current headline stats: Spain atk 1.45 def 0.66 form 0.90; Argentina atk 1.45 def 0.76
   form 0.85 fatigue 0.28; France atk 1.45 def 0.73; England atk 1.45 def 0.86.
2. **Simulation** (`simulator.py`, N=10,000): xG from attack×opp-defence (+form/fatigue/
   set-piece modifiers in predict_xg); knockout λ ×0.85 (KNOCKOUT_DAMPING); per-sim gamma
   multipliers Gamma(k,1/k), CV=0.30 (**GOAL_DISPERSION_CV** — fattens 0-0/tails, trims
   1-1 slightly, means untouched); red cards sampled (sourced coefficients); ET = 30 more
   minutes of the same process, pens ≈ coin flip w/ slight home edge; halves, first-goal
   race, scoreline distribution (top 30), advance probabilities.
3. **Market anchoring:** final probability = 0.60·model + 0.40·implied (MODEL_WEIGHT).
   Kalshi fee 0.07·P·(1−P); netOdds = 1/(P+fee). Board is likelihood-first (edge shown,
   never filtered on).
4. **ET fatigue rule** (`effective_team_stats`): a team whose LAST match went AET gets a
   fatigue bump automatically — hand stats stay clean.
5. **Live remainder** (`price_live`): score-seeded, time-scaled (sim_minutes clamps),
   red-card aware, attack levers × defence (openness) levers × momentum tilt; settled
   first-goal markets dropped; model-first completeness rows (`model:{key}`) where Kalshi
   closed the book. Live edge is informational — the market knows the score. The signals
   system is the ONE deliberate exception, tightly thresholded.

## PART C — LIVE PIPELINE (cadences)
```
15s   live_tick → poll_live_state (ESPN keyless fall-through; API-Football live=all when
      it deigns) → MatchLiveSnapshot upsert; freeze on FT → capture_closing_snapshot +
      resolve_bracket
~25s  live_auto out-cache: stats (30s cache) + commentary (30s cache, same summary fetch)
      → parse_plays → suggest_levers(share, openness, momentum) → price_live
30s   live_signals job: watched BUY/SELL ±8pts; easy-win ≥85%/≤90¢/≥5pt; cooldown 180s
60s   bots tick: entries/exits/settlements
30s   frontend polls: auto stream, stats, signals (toast new, badge rows)
```
ESPN quirks (hard-won): scoreboards bucket by US-EASTERN date (always try kickoff ±1 day);
orient teams BY NAME never homeAway; never cache empty answers; commentary time.value is
seconds. Stoppage: feeds expose only elapsed '+x', never announced added time — running
periods keep a small remainder on the clock (floors 44'/88'/118').

## PART D — THE BOT ARENA (all rules exact)
$1,000 each; fee 0.07·P·(1−P) on entry AND early exit; one position per bot per market
ever; settlement from MarketClosing result (yes/no) else last_price ≥0.95/≤0.05 heuristic,
else wait. `GET /api/bots`.
- 🤓 **KELLY** pre-match; edge ≥5pts, price ∈[10¢,90¢]; stake = cash·f*/2 capped $150,
  f* = (p−c)/(1−c).
- 😌 **CHALK** pre-match; model ≥65% and price ≤85¢; flat $50.
- 🎰 **MOONSHOT** pre-match; price ∈[2¢,20¢] and model/implied ≥1.4; flat $10.
- ⚡ **WIRE** in-play; enters $40 on fresh BUY/easy-win signals; exits on SELL signal or
  +20¢ take-profit (sell-side fee applied).
- 🧊 **FADE** in-play; price crashed ≥15¢ from the T-10 lock price AND live model ≥
  price+8pts; $60; holds to settlement.
- 🍯 **SWEETSPOT** pre-match; modal exact score + neighbours ≥60% of mode (max 4);
  $60 dutched ∝ model p. Backtest on the 4 archived locks: +6.5%.
- 🤝 **CREW v3** (Son & friends, upgraded): even game (win90 gap <20pts) → ladder
  1-0/0-1/2-0/0-2/2-1/1-2; mismatch → stronger side's 2-0/2-1/3-0/3-1/3-2 only.
  Knockouts ALWAYS add 1-1 (both v2 backtest losses were 1-1 at 90; mismatch keeps the
  parked-bus 1-1 hedge); 0-0 added when model P(0-0) ≥6%; 2-2 only when draw ≥25%.
  $60 staked ∝ model probability. Backtest lineage: v1 (both-ways to 3-2, even split)
  −32.6% → v2 (Son's literal description) −4.7% → v3 +47.4% (IN-SAMPLE — fixes derived
  from those same 4 matches; the weekend is out-of-sample).
Proposed, never built: 🪙 COIN (random control), 🐑 SHEEP (model-blind price-follower),
🎯 SNIPER (T-10-only value), 😤 TILT (martingale). Post-final candidates.

## PART E — RESEARCH SYSTEM
- **T-10 lock**: `is_final` Prediction batch at kickoff−10min (the unfudgeable model view).
- **Closing snapshot**: every priced family captured at freeze (idempotent, backfillable —
  Kalshi keeps settled markets queryable).
- **`/api/research/{id}`** = {final_lock, closing, last_readings, result}.
- **research_archive/** in the repo = the copies that survive deploys. Contains: NOR_ENG
  (1-2 AET), ARG_SUI (3-1 AET), SF1 (0-2: Spain), SF2 (1-2: Argentina) full triples,
  MAR_FRA + ESP_BEL closings, live_signals_SF1_SF2.json (the first 10 signals, 7/10 wins).
- **Post-final agenda:** Brier scorecard model-vs-closing across all locks; calibration
  write-up ("my model vs the market over a World Cup knockout stage"); bot leaderboard
  verdict; portfolio README + case study (repo is PUBLIC, has NO README — highest-leverage
  gap); screenshots of the live UI DURING the final (last chance ever); position tracker
  build (entry price → hold/exit EV, the deferred half of the buy/sell feature); Railway
  volume attach.

## PART F — MATCH-DAY RUNBOOK ("run the match loop")
Pre-kickoff: verify freshness + books on the match page; Son watches his markets (Watch
button — survives until next deploy only); T-10 lock fires automatically; lineups ~1h out.
During: poll `/api/live-signals` + live-auto; push signals/goals/momentum flips to Son's
phone (PushNotification; suppressed while he's at the terminal); answer reads from the
live-auto output; NEVER deploy. After FT: freeze + closing capture + bracket resolve are
automatic; bots settle on the next ticks; archive `/api/research/{id}` + signals to
research_archive/ and commit (no push needed until post-final); check bot leaderboard.
Remote Control note: the ORIGINAL build-session lineage has poisoned RC metadata — fresh
sessions connect cleanly via `claude --remote-control "name"` FROM A STANDALONE TERMINAL
(never the desktop app's embedded pane — archiving that window kills the process). Kill
stale `claude --remote-control` processes first (`ps aux | grep remote`). Discord webhook
(unset as of Jul 17) is the RC-independent alert path.

### Discord alert channel (server-side pushes — NOT yet configured)
The RC-independent alert path: `src/alerts.py::send_discord` posts to a webhook and
silently no-ops while `DISCORD_WEBHOOK_URL` is unset (which it is, as of Jul 17 —
alerts currently print to Railway logs only). Fires even when Son's Mac is asleep.

**What flows through it once set:**
- 🟢/🔴 **BUY/SELL signals** on watched markets + 💰 **EASY WIN** sweeps (live, 30s job)
- 🔴 **T-10 final lock** per match (best bet or "SKIP all")
- 🟢 **New TAKE** suggestions from the hourly batch (max 3/match)
- ⏰ **Ripeness alerts** on watched markets crossing the timing threshold
- 🗓️ **Bracket resolutions** ("Quarter-final set: X advances")
Messages cap at 1900 chars; failures log and never raise.

**Setup (5 min):** Discord → your server → channel (e.g. #wc26-alerts) → ⚙ Edit
Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL → Railway
dashboard → wc26-bet-suggester service → Variables → add `DISCORD_WEBHOOK_URL=<url>`.

**⚠ TIMING TRAP:** saving a Railway variable TRIGGERS A REDEPLOY = full DB wipe
(bot ledgers, watchlist, signals). Pre-match ledgers re-enter automatically within
~2 min (rules are deterministic), so setting it is CHEAP before Saturday's kickoff
and FORBIDDEN once matches start (in-play Wire/Fade positions and settled history
would be lost). Either set it before Sat 21:00 UTC — then reprime settings and
verify the 7 ledgers repopulate — or leave it for after the final. Same trap
applies to deleting MIN_CONFIDENCE or attaching the volume: batch all dashboard
changes into ONE pre-Saturday window, or do none.

## PART G — KNOWN DEBT
1. Railway volume at /app/data — THE fix for all ephemerality (user dashboard).
2. `MIN_CONFIDENCE=0.60` env var still set — reprime after every deploy until deleted.
3. API-Football key exposed in chat early on — regenerate (never committed; repo scanned clean).
4. Public repo has no README (portfolio grade D for presentation vs A− engineering).
5. Easy-win double-pings when one outcome has two books (KXWCGAME + KXWCMOV) — accepted
   behavior (prices differ), dedupe by outcome_key if it annoys.
6. Manual panel has attack sliders only (defence folded into attack by design).
7. DEMO_MODE=true default in config — prod runs with real keys via env; local dev runs demo.

## PART H — FUTURE UPDATES: UN-DEPLOYED IDEAS & UNFIXED FLAWS
The complete backlog, consolidated from every session. Nothing here is deployed.

### H1. Committed next builds (Son said yes, waiting on the freeze/post-final)
- **Position tracker** — the deferred half of the buy/sell-alert request ("know which
  bets are no longer worth holding"). Spec sketched: Son records entry price + size per
  real position → live read prices each position's hold-to-settlement EV vs cash-out-now
  value (both fee-aware) → HOLD/EXIT verdicts with P&L, not just directional signals.
  Watchlist is the trigger list today; positions are the missing state.
- **Railway volume at /app/data** (Son's dashboard) — makes ledgers/locks/watchlist/
  signals survive deploys; unlocks post-final analysis without archive gymnastics.
- **Post-final research harvest** — Brier scorecard (locked model vs closing vs result
  across all archived locks), calibration write-up, bot leaderboard verdict, README +
  portfolio case study, screenshots of the live UI during the final (LAST chance).

### H2. Proposed, never green-lit (one word revives any of them)
- **Control bots:** 🪙 COIN (random placebo), 🐑 SHEEP (model-blind price-momentum
  follower — the anti-model control; if it beats KELLY the edge thesis is in trouble),
  🎯 SNIPER (KELLY's rule but only in the last 15 min pre-kickoff — tests bet-early vs
  bet-late, the question the whole ripeness system was built around), 😤 TILT
  (martingale staking — the cautionary ledger).
- **Saved DIY builds** + auto-vs-custom strategy comparison on the match page.
- **Live value of held strategies** — mark-to-market the strategy-tab dutches in play.
- **Half-Kelly chip** in the strategy tab (full Kelly + stake-all exist).
- **Manual-panel first-scorer settlement** (panel knows a goal happened, not who).
- **Crew-mode toggle for real play** — v3 vs Son's crew's actual habits diverge now;
  after the weekend, re-sync the bot to whatever the crew actually adopts.

### H3. Model-improvement candidates (with evidence status)
- **Score-effects / dominance dynamics** — leads snowball (winner's λ up, loser's down
  once ahead): would trim 1-0s and fatten 3-0/4-1 tails, which is EXACTLY the shape of
  this tournament's deviation (4 one-nils seen vs 7.6 expected, p≈0.13; four 3-0s, three
  1-4s). The strongest un-implemented model idea. Needs more data than 41 matches to
  calibrate honestly — a next-tournament project.
- **Minute-aware easy-win thresholds** — SF2's three losing signals were all late-game
  "state holds" bets (draw markets at 88' killed by a 90+2 winner). Idea: raise the
  certainty bar or discount by remaining minutes (e.g. require model ≥ 85% + 0.5pt per
  minute past 80'), or suppress draw-family easy-wins entirely after ~85'.
- **Momentum: per-team defensive effectiveness** — Son's original ask ("who is defending
  and how effective") is only half-served: openness reads the game's volume, momentum
  reads attacking share. A defensive read (opponent pressure vs xGA conceded in-window,
  blocks/clearances from commentary) is the missing third axis.
- **Sweet-spot cluster width tuning** — top-4 caught both QF draws, missed both SF
  winners' scores by one rung; a 6-book cluster catches SF2's 1-2 but halves the QF
  payouts. Needs the Brier data to settle; expose as a config knob first.
- **MODEL_WEIGHT ratchet** — anchoring is 60/40 model/market; raise toward 1.0 only as
  the calibration record earns it (the config comment already promises this).
- **Constants that are estimates, tunable against data:** KNOCKOUT_DAMPING 0.85,
  GOAL_DISPERSION_CV 0.30, _SHOTS_PER_XG 8.0, momentum caps/half-life, lever caps.
- **Dixon–Coles low-score correlation — investigated and REJECTED** (Jul 16): this
  tournament shows no draw deficit (17% actual vs 16% predicted 0-0/1-1). Keep rejected
  unless future data disagrees; documented so nobody re-litigates it blind.
- **Champion-forecast jitter** — the bracket forecast re-simulates per request, so the
  headline flaps (Spain 50.1 ↔ Argentina 50.8 across refreshes near a coin flip). Fix:
  cache per prediction batch or pin the sim seed per batch.

### H4. Unfixed flaws & quirks (severity-ordered)
1. **Everything-ephemeral on deploy** (CRITICAL until the volume): ledgers, locks,
   watchlist, signals. Self-heal covers results/closings/bracket only.
2. **MIN_CONFIDENCE=0.60 env var** resets settings every deploy — reprime ritual required.
3. **Exposed API-Football key** (pasted in chat July ~5, never committed) — regenerate.
4. **Duplicate-outcome book rows** — Kalshi lists the same outcome in two families
   (KXWCGAME 3-way + KXWCMOV moneyline): the markets table shows two "Spain to win"
   rows and the easy-win sweep can ping both (~60s apart). Dedupe by outcome_key,
   keeping the better price, or accept as price comparison.
5. **Public repo has no README** — portfolio presentation grade D vs A− engineering.
6. **Desktop-app Remote Control** — the original build-session lineage carries poisoned
   RC metadata ("session_url" disconnect error); desktop app bundles its own Claude Code
   (2.1.209) independent of the CLI (2.1.212). Fresh sessions + CLI `--remote-control`
   work; watch for zombie `claude --remote-control` processes (one spun for 8 days).
7. **Browser-pane screenshots of namson.dev render black** (automation quirk; DOM reads
   fine) — irrelevant to users, annoying for verification.
8. **Kambi/DraftKings reference odds** — fallback chain works but exact-score coverage
   needs a paid API-Football key; reference tab is thinner than designed.
9. **suggest_levers neutral when ESPN stats lag** — early minutes of a match run on
   cumulative-share defaults until the boxscore populates (~5-10 min). Acceptable;
   momentum partially compensates once commentary flows.

### H5 — Jul 17 pre-handoff audit: newly disclosed items (code-verified)
Found by the final audit, NOT fixed (freeze); none threaten the weekend. Fix post-final.

**Bot-arena fill realism (affects leaderboard interpretation, not correctness):**
1. **All bot fills happen at the ASK, including sells.** WIRE's exits and every
   settlement-free close use `market_probability` (ask) minus fee — real sells hit the
   BID. Paper P&L is optimistic by the spread, which on thin WC books can be 2-5¢.
   The bots compare fairly against EACH OTHER (same optimism), not against reality.
2. **WIRE enters at the signal's captured price**, up to ~90s stale by tick time.
3. **Equity is at-cost, not mark-to-market** (`/api/bots`: equity = cash + open cost).
   Net P&L shows REALIZED results only — the weekend leaderboard lags reality until
   books settle. Post-final fix: mark open positions at the live/last price.
4. **CREW/SWEETSPOT ladders can assemble across ticks** as rungs get priced/repriced;
   per-match spend can drift a few dollars around $60. Bounded, benign.

**Model/simulator:**
5. **ET/pens continuation runs WITHOUT gamma dispersion** (`_continuation_phase` skips
   `_dispersed`; regulation + pre-match paths have it). Unify post-final.
6. **VAR-disallowed goals linger in the momentum pattern** briefly (±12% cap bounds it).
   Own goals are (correctly) skipped as threat events.

**Process-restart semantics (benign):**
7. In-memory state resets on backend restart: the final-lock once-guard (worst case: a
   duplicate is_final batch — harmless, newest wins) and the signals cooldown state
   (worst case: one repeat signal — WIRE's dedupe absorbs it).
8. Railway nuance: a same-instance CRASH-restart likely keeps the SQLite file; only
   redeploys/instance migrations are guaranteed wipes. Don't rely on it.

**Cosmetic:** unused `import math` in src/bots.py (delete in the next real commit).

---

## V5.1 ADDENDUM (Jul 17 evening) — the Desktop sync + disk incident
At ~17:16 Jul 17 the sync provider owning `~/Desktop` was disabled/uninstalled
("SYNC DISABLED (app not installed)" per the sync daemon), turning
`Desktop/Projects/WC26 Predictor` into **5,317 dataless stubs** (124 files survived):
all 45 PMSR PDFs + the extracted/ dataset, ALL V1–V5 PROJECT_DOC archives + zips, old
bracket images, logs. Reads hang (ETIMEDOUT); `brctl download` reports the cloud copies
don't exist. One casualty pre-dated the discovery: `.claude/settings.local.json` was
replaced with `{}` to unblock session startup (permissions re-approve on use).
The same evening the startup disk hit 100% (0 bytes free): ~19GB of deleted Logic Pro
projects were sitting in iCloud Drive's local `.Trash`. They were RESCUED (moved intact
to `~/Music/Recovered/` — 8 projects, 19GB verified) and caches purged for headroom.
NOTE: the disk remains tight; the music should move to an external drive.

**Safe (in `~/dev`, never synced):** both git repos entirely — code, tests,
research_archive/, player_rates.json, fifa_manifest.json (with every PMSR URL).

**Recoverable by pipeline:** the PMSR corpus — `scripts/fifa_extract.py --dir <NEW
NON-DESKTOP DIR>` re-downloads all 45 PDFs from the manifest and rebuilds extracted/.
CAUTION: do NOT point it at the ghosted match_pdfs dir (dataless stubs exist by name —
the downloader would skip them and extraction would hang). Needed before the next stats
update (M103/M104 post-weekend).

**Recoverable only by Son:** re-enable/reinstall whatever sync app owned Desktop (check
recently-removed apps ~17:16 Jul 17, or iCloud Desktop & Documents toggle), check
icloud.com → Drive → Recently Deleted, or Time Machine. V1–V4 PROJECT_DOC history is
only there. This V5 doc was regenerated from the authoring session's context into the
repo (docs/PROJECT_DOC_V5.md, branch docs-v5-handoff) — the V4/V3 verbatim history it
originally carried is NOT in this copy; its outline: V4 snapshot (Jul 10) over a V3 doc
(Jul 8-9) with parts 0-9: TL;DR, goal/philosophy, architecture, model layer, what's
built, live in-play system, known debt, operational runbook, how-to-resume, UI/UX
redesign.

**Standing rule going forward: nothing project-critical lives on the Desktop.** Docs go
in the repo (this file); archives go in the repo; the Desktop folder is scenery until
Son restores the sync provider.
