# WC26 Bet Suggester — Project Report (Resume / Portfolio Edition)

*V7 edition, July 22, 2026 — three days after the final, one day after the independent evaluation whose findings this report incorporates. For use in Son's resume, portfolio
site, and interviews. Source, committed research artifacts, and the deterministic
V1 scoring are verifiable from the public repo; operational history (commit
counts, outages, wipe/restore cycles, alert delivery) is project record.*

---

## The one-liner

> **A full-stack quantitative sports-prediction platform that priced every Kalshi
> World Cup 2026 market against a Monte Carlo simulator, repriced matches live from
> real feeds, ran a twelve-bot paper-trading laboratory with designed controls — and
> called the world champion at the model freeze.**

**Live:** namson.dev/bet-suggester · **Backend:** Railway (FastAPI) · **Stack:** Python
3.12, FastAPI, SQLAlchemy, APScheduler / Next.js, TypeScript, Tailwind v4

---

## What it is

A personal research tool built and operated **during** the World Cup itself (June–July
2026), in production for the entire knockout stage. Four subsystems:

1. **The model.** A Monte Carlo match simulator (10,000 runs/prediction): Poisson goal
   process with gamma overdispersion, opponent-adjusted xG team ratings hand-built from
   FIFA's official post-match reports (47 PDFs, pipeline-extracted), knockout damping,
   red-card sampling, extra-time/penalties continuation, and market anchoring
   (60% model / 40% market-implied). Output: win/draw/advance probabilities, exact-score
   distributions, and — since the Jul 22 economics unification — net-of-fee edge
   and EV for every Kalshi market from one shared execution module.

2. **The live pipeline.** A 15-second polling loop over free, keyless public feeds
   (ESPN) that reprices matches **in play**: score- and time-seeded re-simulation,
   possession/shot "openness" levers, and a play-by-play momentum parser that converts
   live text commentary into decayed, weighted threat events (±12% attack tilt). On top
   of it: automated BUY/SELL signals on watched markets, an "easy-win" sweep over every
   open book, and a hold-vs-cashout **position tracker** for real holdings — all fanned
   out server-side to Discord (two channels: TL;DR actions + detailed narration) and
   phone push, independent of any client being open.

3. **The bot arena.** Twelve paper-trading bots, $1,000 each, trading real market books
   with the exchange's real fee model, automatic settlement from captured closing
   snapshots, and a public leaderboard — *designed as an experiment*, with controls
   (details below).

4. **The research system.** At kickoff−10 minutes, the model's view of every match is
   frozen ("T-10 lock"); at full time, the closing state of every priced market family
   is captured. The archive holds **six complete prospective market-level
   lock/closing/result bundles** (both late QFs, both SFs, THIRD, FINAL), **eight
   labeled reconstructed winner calls** (re-simulated from the commit deployed at
   each kickoff), and two excluded matches that predate the repo — every record
   carries its evidence label, and the archive survives the infrastructure.

---

## Headline outcomes

- **The model called the champion.** At the final's T-10 freeze: **Spain 53.9%** — Spain
  won 1–0 AET. The frozen "sweet-spot" scoreline cluster (1-1/1-0/0-1/0-0, ≈38% of
  simulations) contained both the 90-minute score (0-0) and the final score (1-0).
- **In a two-match paper pilot, the strategy ordering matched the thesis** — the
  model-driven Kelly bot finished first (+45.5% on bankroll), the model-blind
  price-follower last (−11.9%), the random placebo in between. Stated plainly:
  the settled ledger spans two matches and the ordering is dominated by
  correlated positions on one match direction, so this is an operational pilot
  with good instrumentation, not evidence of a general strategy ranking. The
  instrumentation — controls, frozen states, archived settlement — is the claim.
- **The model's accuracy is quantified, competitive, and honestly bounded.**
  Winner calls: **11 of 14 knockout matches** (one-sided binomial p = 0.029;
  two-sided 0.057 — suggestive, not proven). Probability quality: Brier 0.0898
  vs 0.0911 across 293 frozen pre-match predictions — **statistical parity with
  a real-money exchange's executable prices** (cluster-bootstrap CIs straddle
  zero). Calibration: lower expected calibration error under the primary 10-bin
  specification (0.0269 vs 0.0384), with the ordering binning-sensitive and the
  cluster-level difference not significant — competitive, not superior.
  Discrimination identical (AUC 0.893 vs 0.890). On the single match where model
  and market picked different winners (SF2), the model was right. Full write-up
  with evidence-hierarchy labels: `docs/V7/CALIBRATION.md`.
- **Operated in production through the event it modeled.** One 15-minute outage all
  tournament (found, diagnosed, fixed, and regression-tested same-day). Deploys are
  now FULLY SELF-HEALING: results, bracket, and the settled bot ledger rebuild at
  boot from public feeds and committed artifacts, with zero operator action (the
  manual restore procedure was proven ×12 before being retired). The public API
  runs fail-closed read-only with authenticated operator controls, verified by a
  mechanical acceptance script sweeping every mutation in the OpenAPI document.
- **Independently evaluated — and improved by it.** A third-party technical and
  quantitative evaluation (Jul 21) confirmed the test suite, reproduced the
  metrics, and found four real defects (set-piece double counting, first-goal
  mixture math, fee-incomplete Kelly sizing, ask-side exit valuation) plus
  several inflated claims; all defects were fixed same-day with regression
  tests, the public API moved to fail-closed read-only with authenticated
  operator controls, and the claims were revised throughout, this document
  included. The evaluation's summary — "the engineering system is currently
  stronger than the evidence for market edge" — is adopted here as the
  project's own position.

---

## The experiment (the part I'd talk about first)

The bot arena is not a gimmick — it's a controlled test of one question: **does the
model have edge, or does it just have confidence?**

| Bot | Role in the experiment | Result |
|---|---|---|
| 🤓 KELLY — half-Kelly staking on model edge ≥5pts | The thesis | **+454.73, 1st** |
| 🐑 SHEEP — model-blind, buys whatever the market bid up | The anti-thesis control | **−119.25, 12th** |
| 🪙 COIN — seeded-random picks | The placebo | −31.93, mean-reverted |
| 🎯 SNIPER — KELLY's rule, only in the last 15 min | Bet-early vs bet-late | +282.48 (2 trades) |
| 😤 TILT — martingale on favourites | The cautionary tale | −52.82 |
| 📚 SCHOLAR — copies peer-weighted consensus, bans loss-making families | Can you learn from the fleet? | +66.71 |
| + six more personas (flat-stake chalk, longshots, in-play scalper, crash-fader, exact-score dutcher, a friend-group recipe) | Strategy coverage | between |

The honest reading, stated as such everywhere the leaderboard appears: **the settled
ledger is a two-match pilot; paper fills are ask-side optimistic by the spread; the
winner's P&L is four correlated positions on one match direction plus one loss; and
the live Kelly rule, flat-staked across all six archived lock matches, was
negative (−11.2%) — the bankroll result came from stake sizing and window.** The
methodological point is that the system was built to *make those sentences
knowable*: controls, frozen pre-match model states, captured closing lines, and
settlement from archived market data rather than self-reported P&L.

The out-of-sample discipline cut the other way too, and that's kept in the record: a
friend-group betting recipe backtested at +47% in-sample went **1-15** in live
out-of-sample play. Both results are published the same way.

---

## The model's report card

Every claim below is computed from frozen pre-match predictions joined to exchange
settlement data, committed in-repo, reproducible with one script.

**The knockout scorecard — 11 of 14 winner calls (78.6%):**

| | |
|---|---|
| Round of 16 | 5/6 — only miss: Norway over Brazil, the tournament's biggest upset (model 62%) |
| Quarterfinals | 4/4 — including Switzerland 50.8%: the match finished 0-0 and went to penalties |
| Semifinals | 1/2 — and SF2 was the only match all tournament where model and market disagreed on the winner; the model won |
| Third place + Final | 1/2 — the final called at 54.7% Spain, champion called at the freeze at 53.9% |

(Two R16 matches are excluded because the repo's first commit post-dates their
kickoffs — the model was built mid-round. Six calls come from tamper-proof frozen
locks; eight are reconstructed by re-simulating with the exact git commit deployed
at each kickoff, and labeled as such.)

**The statistics** (revised after independent evaluation; benchmark = the
executable ask, an execution comparison rather than a neutral forecast):

| test | result | reading |
|---|---|---|
| Winner calls vs chance | 11/14; one-sided p = 0.029, two-sided p = 0.057 | suggestive — clears 5% only one-sided |
| Advance Brier vs coin flip | 0.2097 vs 0.2500; bootstrap CI brushes zero | borderline, honestly labeled |
| Brier vs the exchange (293 markets) | 0.0898 vs 0.0911, cluster CI straddles zero | statistical parity with a real-money market |
| Expected calibration error | 0.0269 vs 0.0384 under the primary 10-bin spec; ordering flips under other binnings | competitive — not established superiority |
| Discrimination (AUC) | 0.893 vs 0.890 | identical |
| Per-family Brier | model ahead in 7 of 9 | broad, not cherry-picked |
| Replay, retrospective raw-edge rule | 28 bets, +3.0% ROI after real fees | descriptive replay only |
| Replay, the live Kelly gate (anchored edge) | 17 bets, **−11.2% ROI** flat-staked | the bot's +45% came from sizing + window — published anyway |

**The one-line verdict, as published:** *probability-precise, not clairvoyant —
and honestly bounded*: suggestively better than chance, statistically tied with
the exchange's executable prices, competitive on calibration under every
specification, with the measurable edge concentrated in humility: every missed
call came at ≤62% confidence, while the market lost two ~70% calls.
Weaknesses documented with equal prominence: an overconfident 40–50% band and a
totals family that lost to the market.

---

## Engineering highlights

**Quantitative modeling**
- Opponent-adjusted team ratings from raw event data: `xGF_adj = xGF·(oppElo/1650)`
  per game, folded incrementally across a team's tournament; incremental folds
  validated against full recomputation to 4 decimal places.
- Gamma-mixed Poisson (CV = 0.30) tuned to fatten 0-0 and blowout tails — a change
  that moved 0-0 into the final's predicted cluster, where the actual 90 minutes
  landed. Rejected ideas are documented with evidence (Dixon–Coles low-score
  correlation: tested, no draw deficit in this data, rejected — written down so it
  never gets re-litigated blind).
- Fee-aware everything: Kelly staking solved by ternary search over the exchange's
  actual 0.07·P·(1−P) fee curve, on both entry and early exit.

**Live systems**
- A resilient keyless feed layer over ESPN with hard-won quirk handling (US-Eastern
  date bucketing, name-based team orientation, empty-answer cache poisoning,
  commentary-time units), because the "official" football API's free tier turned out
  to be season-blind for 2026.
- NLP-lite momentum: typed, weighted threat events parsed from live commentary text,
  12-minute window, 6-minute half-life, capped tilt — validated against a real
  semifinal (the losing late signal fired in a minute where the pattern read the team
  at 23% of recent threat).
- Everything alert-worthy is server-side and client-independent: two-channel Discord
  webhooks + ntfy.sh phone push, with a test endpoint to prove channels before a match.

**Production operations under adversity**
- The platform's database was **ephemeral by infrastructure** (SQLite in a container;
  every deploy wipes it). Instead of being blocked, the system grew a layered answer:
  a boot-time self-heal that rebuilds all match results and the full bracket from
  dated public feeds (fixpoint iteration over bracket dependencies, ~2 minutes on a
  fresh container), idempotent re-capture of market closings, an idempotent ledger
  restore endpoint, and an export-first deploy procedure — **eight lossless wipes**
  including two on the same day.
- Incident record, all found in production during the event: a timezone-normalization
  crash (SQLite round-trips naive datetimes; comparison against aware timestamps threw
  every tick for 15 minutes — the tournament's only outage), a settlement key mismatch
  (`last_price` vs `last_price_dollars`), and an accounting bug where closed positions
  refunded their cost. Each: diagnosed from logs, fixed, regression-tested, deployed
  same-day, and written into the project doc.

**Frontend craft**
- A four-league platform shell (World Cup / MLS / EPL / La Liga) with a "drive-mode"
  switcher: per-league theme systems (CSS custom properties, official-mark-adjacent
  typefaces) and bespoke full-page reveal transitions — all compositor-only after a
  performance pass (transform/opacity only; no clip-path, gradient, or layout
  animation on large surfaces; effect windows sized to outlive their slowest child).
- BigInt-exact payout mathematics in the strategy builder; fee-aware dutching;
  a bracket view; asset pipeline work (flood-fill keying, supersampled rebuilds,
  exact-color inking of official marks).

**Testing & discipline**
- 296 tests, green across the whole arc — including regression tests for every
  production incident, and "time-bomb" tests (assertions that would rot when the
  tournament ended) hunted down and defused so the suite stays green forever.
- Gated pushes (test exit codes never masked by pipes), research data archived before
  every deploy, secrets kept out of the repo (and the one deliberate
  tournament-weekend exception — a public push topic — documented with its rotation
  plan).

---

## By the numbers

| | |
|---|---|
| Duration | ~6 weeks, solo, built during the live tournament |
| Backend | 103 commits · ~9,200 LOC source · ~4,600 LOC tests · 327 tests green |
| Frontend | 102 commits · ~6,300 LOC TypeScript/CSS |
| API surface | ~37 endpoints; 8 scheduled jobs (15s → hourly cadences) |
| Markets priced | 9 Kalshi market families, every WC26 fixture, pre-match + in-play |
| Research corpus | 6 complete frozen bundles + 8 labeled reconstructions + 2 excluded |
| Data pipeline | 47 official FIFA match reports → extracted xG/player rates |
| Bots | 12 personas, 84 settled positions, public leaderboard |
| Production record | One 15-min outage; deploys fully self-healing (manual restore retired after ×12); alerts Mac-independent |
| Winner calls | 11/14 knockout matches (one-sided p = 0.029, two-sided 0.057) |
| vs the market | Brier 0.0898 vs 0.0911 — parity (cluster CIs straddle zero) |
| The call | Spain 53.9% at freeze → Spain champions |

---

## Resume bullets (ready to paste)

**Short (one line):**
> Built and operated a full-stack sports prediction-market platform (Python/FastAPI,
> Next.js/TS) live through the 2026 World Cup — Monte Carlo pricing of 9 Kalshi market
> families, real-time in-play repricing, and a 12-bot controlled trading experiment;
> the model called 11 of 14 knockout winners (one-sided p=0.029) including the
> champion, and held statistical parity with the exchange it priced against.

**Standard (3 bullets):**
> - Designed a Monte Carlo match-simulation engine (Poisson + gamma overdispersion,
>   opponent-adjusted xG from 47 official match reports, fee-aware Kelly staking) that
>   priced every Kalshi World Cup 2026 market and froze auditable pre-match
>   predictions; it called 11 of 14 knockout winners (one-sided binomial p=0.029)
>   including the champion, and held statistical parity with the exchange on both
>   Brier score (0.0898 vs 0.0911 over 293 frozen markets) and calibration —
>   every claim bounded by match-cluster confidence intervals and verified by an
>   independent evaluation.
> - Designed a controlled paper-trading pilot — 12 bots including random-placebo and
>   anti-model controls — on live exchange books with real fee/settlement modeling;
>   in the two-match settled window the model-driven strategy finished 1st and the
>   model-blind control last, published as a pilot with its correlation and
>   fill-optimism caveats in the headline, not the footnotes.
> - Operated the system in production through the tournament on deliberately hostile
>   infrastructure (ephemeral DB wiped on every deploy): built fixpoint boot-time
>   self-healing from public feeds, idempotent restore endpoints, and an export-first
>   deploy procedure, later upgraded to fully self-healing boot recovery; hardened
>   post-tournament to a fail-closed read-only API with authenticated operator
>   controls — one 15-minute outage all tournament, 327 tests green.

**Portfolio-site blurb (a paragraph):**
> During the 2026 World Cup I built, deployed, and operated a prediction-market
> research platform end-to-end: a Monte Carlo simulator hand-fed with
> opponent-adjusted xG extracted from FIFA's official match reports, live in-play
> repricing driven by a play-by-play momentum parser, server-side alerting, a
> hold-vs-cashout tracker for real positions, and a twelve-bot trading laboratory
> designed as a controlled experiment (with a random placebo and an anti-model
> control). The model called the champion at its tamper-proof pre-match
> freeze; the experiment's final ordering — model-driven first, model-blind last —
> is archived in-repo along with the frozen predictions, closing lines, and results
> for every knockout match, so every claim on this page is checkable.

---

## Interview talking points (STAR-ready)

1. **"Tell me about a production incident."** The naive-datetime crash: new bot logic
   compared SQLite's naive `created_at` against timezone-aware cutoffs; every 60s tick
   crashed for 15 minutes during the tournament. Diagnosed from Railway logs, fixed by
   normalizing tzinfo, added a regression test, deployed within the hour — and
   extracted the durable lesson (the codebase compares datetimes in SQL, not Python)
   into the project doc so the class of bug can't recur silently.

2. **"Design under constraints you don't control."** The database was ephemeral (no
   volume on the deploy platform) and the event was un-pausable. Rather than gamble,
   the system treats the wipe as a first-class event: self-healing result
   reconstruction from public feeds (a fixpoint loop, because knockout brackets have
   data dependencies), idempotent market-closing recapture, an idempotent ledger
   restore API, and archives committed to git as the durable store. Eight wipes, zero
   data loss.

3. **"How do you know your model is actually good?"** Three mechanisms: frozen T-10
   predictions that can't be retro-fitted; captured closing lines as the market's
   answer to the same question; and designed controls in the bot experiment (placebo +
   anti-model). The evaluation was then done properly — cluster bootstrap (matches as
   clusters, because markets within a match are correlated), binomial tests, ECE, AUC
   — and the answer is nuanced: suggestive against chance (one-sided p=0.029,
   two-sided 0.057), statistically *tied* with the market on Brier, and
   *competitive* on calibration (lower ECE under the primary binning, with the
   ordering binning-sensitive and the cluster-level difference not significant). Then the discipline of publishing the caveats
   with the wins — and the losses: an in-sample +47% backtest that went 1-15
   out-of-sample is in the same public record.

4. **"A performance problem you solved."** League-switch animations janked: the causes
   were full-page clip-path/gradient animation (paint storms) and a dashboard
   unmount/remount on switch (main-thread stall). Fix: everything compositor-only
   (transform/opacity, pre-painted surfaces, effects windowed to outlive their
   children) and the heavy view permanently mounted behind CSS visibility. Verified by
   frame-feel on-device, not just principle.

5. **"Working with messy external data."** ESPN's free feeds bucket days in US-Eastern
   time, mislabel extra-time intervals as halftime, report stoppage only as elapsed
   text, and list teams in inconsistent order; the official football API's free tier
   couldn't see the 2026 season at all. Every quirk is handled, documented inline, and
   most importantly *cached correctly* (never cache an empty answer).

---

6. **"Tell me about receiving critical feedback."** The project was independently
   evaluated by a third party after the tournament. Instead of defending it, I
   verified every claim against the code — confirming four real defects my own
   audits had missed — then fixed all four with regression tests, rebuilt the
   public deployment fail-closed, moved the statistics into a deterministic
   pinned pipeline, and rewrote every overstated claim, all within roughly a
   day. The evaluation's summary line is now quoted in the project's own
   documentation as its official position.

---

## Skills demonstrated

**Quantitative:** Monte Carlo simulation · Poisson/gamma processes · probability
calibration · Kelly criterion & fee-aware staking · experiment design with controls ·
honest small-sample statistics
**Backend:** Python · FastAPI · SQLAlchemy · APScheduler · REST design · idempotent
API design · webhook/push integrations (Discord, ntfy)
**Data:** PDF extraction pipelines · feed normalization · time-series capture ·
archival/reproducibility discipline
**Frontend:** TypeScript · Next.js · Tailwind · CSS animation performance
(compositor-only) · design systems (multi-theme) · BigInt-exact financial math
**Operations:** locally test-gated deploys · production debugging from logs · self-healing
systems on ephemeral infrastructure · incident writeups · secrets hygiene
**Product:** built for a real user with real money on the line (the position tracker's
EXIT/HOLD verdicts ran live during the final), scoped and shipped under a hard,
immovable deadline: kickoff.

---

## Links

- **Live app:** https://namson.dev/bet-suggester (World Cup mode: the champions' gold
  edition; Bot Arena: the settled leaderboard)
- **Backend repo:** github.com/SonNguyen2914/wc26-bet-suggester — see
  `research_archive/` for the frozen-prediction corpus and
  `docs/V7/PROJECT_DOC.md` for the full technical documentation
- **Frontend repo:** github.com/SonNguyen2914/namson-dev

*The full evaluation lives in `docs/V7/CALIBRATION.md`: the 293-market three-stream
scoring, the 14-match knockout scorecard (frozen locks + labeled git-archaeology
reconstructions), the significance battery (cluster bootstraps, both-sided binomial,
multi-specification ECE, AUC), both trading replays, and the evidence-hierarchy
labels. One deterministic, seeded script now genuinely reproduces all of it from
committed data (`scripts/score_calibration.py` →
`research_archive/calibration_results.json`), and
`tests/test_calibration_pipeline.py` fails the suite if the narrative numbers ever
drift from the computation. The project was independently evaluated on Jul 21;
its findings are incorporated throughout.*
