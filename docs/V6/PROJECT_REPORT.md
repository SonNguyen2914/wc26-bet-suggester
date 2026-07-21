# WC26 Bet Suggester — Project Report (Resume / Portfolio Edition)

*Prepared July 21, 2026 — two days after the final. For use in Son's resume, portfolio
site, and interviews. Everything here is verifiable from the public repo, the committed
research archive, and the live deployment.*

---

## The one-liner

> **A full-stack quantitative sports-prediction platform that priced every Kalshi
> World Cup 2026 market against a Monte Carlo simulator, repriced matches live from
> real feeds, ran a twelve-bot paper-trading laboratory with designed controls — and
> called the world champion at the model freeze.**

**Live:** namson.dev/bet-suggester · **Backend:** Railway (FastAPI) · **Stack:** Python
3.11, FastAPI, SQLAlchemy, APScheduler / Next.js, TypeScript, Tailwind v4

---

## What it is

A personal research tool built and operated **during** the World Cup itself (June–July
2026), in production for the entire knockout stage. Four subsystems:

1. **The model.** A Monte Carlo match simulator (10,000 runs/prediction): Poisson goal
   process with gamma overdispersion, opponent-adjusted xG team ratings hand-built from
   FIFA's official post-match reports (47 PDFs, pipeline-extracted), knockout damping,
   red-card sampling, extra-time/penalties continuation, and market anchoring
   (60% model / 40% market-implied). Output: win/draw/advance probabilities, exact-score
   distributions, and fee-aware edge for every Kalshi market on every fixture.

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
   is captured. Lock + closing + result triples for **all 16 knockout matches** are
   archived in-repo — a complete dataset for model-vs-market calibration that survives
   the infrastructure (see "war stories").

---

## Headline outcomes

- **The model called the champion.** At the final's T-10 freeze: **Spain 53.9%** — Spain
  won 1–0 AET. The frozen "sweet-spot" scoreline cluster (1-1/1-0/0-1/0-0, ≈38% of
  simulations) contained both the 90-minute score (0-0) and the final score (1-0).
- **The experiment returned the predicted ordering.** Over the tournament's final
  stretch, out-of-sample, on real books: the model-driven Kelly bot finished **first
  (+45.5% on bankroll)**, the model-blind price-follower finished **last (−11.9%)**, and
  the random-placebo control mean-reverted in between — exactly the shape the edge
  thesis predicts. (Honest caveats attached; see below.)
- **Operated in production through the event it modeled.** One 15-minute outage all
  tournament (found, diagnosed, fixed, and regression-tested same-day). Every deploy
  survives a full database wipe losslessly — by designed procedure, proven eight times.

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

The honest reading, stated as such everywhere the leaderboard appears: **one weekend is
a small sample; paper fills are ask-side optimistic by the spread; the ordering — model
first, anti-model last, placebo in the middle — is evidence, not proof.** The
methodological point is that the system was built to *make that sentence possible*:
controls, frozen pre-match model states, captured closing lines, and settlement from
archived market data rather than self-reported P&L.

The out-of-sample discipline cut the other way too, and that's kept in the record: a
friend-group betting recipe backtested at +47% in-sample went **1-15** in live
out-of-sample play. Both results are published the same way.

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
| Backend | 93 commits · ~8,700 LOC source · ~4,200 LOC tests · 296 tests green |
| Frontend | 101 commits · ~6,300 LOC TypeScript/CSS |
| API surface | ~37 endpoints; 8 scheduled jobs (15s → hourly cadences) |
| Markets priced | 9 Kalshi market families, every WC26 fixture, pre-match + in-play |
| Research corpus | 16 knockout matches × (frozen model + closing book + result) |
| Data pipeline | 47 official FIFA match reports → extracted xG/player rates |
| Bots | 12 personas, 84 settled positions, public leaderboard |
| Production record | One 15-min outage; 8 lossless deploy-wipes; alerts Mac-independent |
| The call | Spain 53.9% at freeze → Spain champions |

---

## Resume bullets (ready to paste)

**Short (one line):**
> Built and operated a full-stack sports prediction-market platform (Python/FastAPI,
> Next.js/TS) live through the 2026 World Cup — Monte Carlo pricing of 9 Kalshi market
> families, real-time in-play repricing, and a 12-bot controlled trading experiment;
> the model called the champion at its pre-match freeze.

**Standard (3 bullets):**
> - Designed a Monte Carlo match-simulation engine (Poisson + gamma overdispersion,
>   opponent-adjusted xG from 47 official match reports, fee-aware Kelly staking) that
>   priced every Kalshi World Cup 2026 market and froze auditable pre-match predictions;
>   its final-match freeze called the champion (Spain, 53.9%) and its predicted
>   scoreline cluster contained the actual result.
> - Ran a controlled paper-trading experiment — 12 bots including random-placebo and
>   anti-model controls — on live exchange books with real fee/settlement modeling;
>   the model-driven strategy finished 1st (+45%) and the model-blind control last,
>   with all caveats (sample size, fill optimism) published alongside.
> - Operated the system in production through the tournament on deliberately hostile
>   infrastructure (ephemeral DB wiped on every deploy): built fixpoint boot-time
>   self-healing from public feeds, idempotent restore endpoints, and an export-first
>   deploy procedure — 8 lossless wipes, one 15-minute outage all tournament, 296
>   tests green.

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
   anti-model). Then the discipline of publishing the caveats with the wins — and the
   losses: an in-sample +47% backtest that went 1-15 out-of-sample is in the same
   public record.

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
**Operations:** CI-gated deploys · production debugging from logs · self-healing
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
  `docs/V6/PROJECT_DOC.md` for the full technical documentation
- **Frontend repo:** github.com/SonNguyen2914/namson-dev

*Suggested next artifact (data is ready in-repo): the Brier/calibration write-up —
"my model vs. the closing line across a World Cup knockout stage" — turns the
leaderboard anecdote into a statistical result and would headline the README.*
