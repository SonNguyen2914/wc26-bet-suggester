# Model vs Market — the Calibration Write-up

*(V7 home — moved from docs/V6 on Jul 22; content unchanged, all claims as revised per the independent evaluation.)*

*July 21, 2026. The post-tournament accounting the research system was built for:
the model's frozen pre-match probabilities, scored against what actually settled,
side-by-side with the market's own price at the same moment.*

## The data

Every match got a **T-10 lock**: at kickoff−10 minutes, the model's probability for
every open Kalshi market on the fixture was frozen to the DB — and archived to the
repo, because locks are the one thing a deploy wipe can't restore. Each lock row
carries the market's **implied probability at that same moment**, making a fair
same-instant comparison possible. Settlement truth comes from captured closing
snapshots, completed today by a backfill against Kalshi's public API (settled markets
stay queryable — `research_archive/settlements_backfill_2026-07-21.json`).

**Coverage: 293 markets across 6 matches** — both quarterfinals that post-date the
archiving discipline (NOR_ENG, ARG_SUI), both semifinals, THIRD, and the FINAL.
100% of locked markets have settlement truth. Excluded, honestly: MAR_FRA and
ESP_BEL (their locks died in a deploy wipe before the archive-first rule existed)
and the whole group stage (the system was born mid-tournament). Base rate: 16.0% of
locked markets settled YES (exact-score books dominate the row count).

## Three probability streams

The stored lock probability is the **anchored** blend the app actually quotes:
`p_anchored = 0.60·p_raw + 0.40·implied` (`MODEL_WEIGHT`). Since the lock also
stores `implied`, the raw simulator number is algebraically recoverable:
`p_raw = (p_anchored − 0.40·implied)/0.60`, clipped to [0,1]. So each market is
scored three ways:

| stream | meaning |
|---|---|
| **RAW** | the pure Monte Carlo simulator, no market influence |
| **ANCHORED** | the 60/40 blend the app quotes (what the bots traded on) |
| **MARKET** | Kalshi's implied probability at T-10 — **the executable ASK** |

**Benchmark semantics (Jul 21 evaluation):** the archived market number is the
buy-side ask (deliberate for execution math; thin-book midpoints are untradeable).
That makes every model-vs-market row here an **execution comparison**, not a
neutral forecast benchmark — asks carry spread, which flatters the model as a
forecaster. The locks did not archive the full book, so a no-vig/midpoint
benchmark cannot be reconstructed for this tournament; the next-competition lock
schema captures bid/ask/depth so it can be.

## Headline result

| stream | Brier ↓ | Log loss ↓ | Skill vs market |
|---|---|---|---|
| RAW model | 0.0898 | 0.2887 | **+1.4%** |
| ANCHORED | **0.0896** | **0.2874** | **+1.7%** |
| MARKET | 0.0911 | 0.2898 | — |

**The honest sentence:** over 293 markets, the raw model scored *slightly better than
the exchange's own prices*, and the 60/40 blend scored better than either parent.
The margins are small and the rows are correlated (many markets share one match's
outcome), so this is "kept pace with the market, maybe a nose ahead" — which, for a
hand-built solo model priced against a real exchange across a knockout stage, is the
result worth having. It is NOT "crushed the market," and nothing here should be
quoted as that.

**The actionable conclusion:** the anchored blend beat the raw model. The
`MODEL_WEIGHT` ratchet question ("earn the right to trust the model more than
60/40") is answered by this data: **don't ratchet — the blend is doing its job.**
Revisit only with a bigger corpus.

## Per match (Brier: raw | anchored | market)

| match | N | raw | anchored | market | better |
|---|---|---|---|---|---|
| NOR_ENG (QF, 1-2 AET) | 50 | 0.0932 | 0.0895 | **0.0858** | market |
| ARG_SUI (QF, 3-1 AET) | 46 | 0.0921 | 0.0844 | **0.0770** | market |
| SF1 (0-2 Spain) | 50 | **0.0828** | 0.0863 | 0.0922 | model |
| SF2 (1-2 Argentina) | 47 | **0.0653** | 0.0681 | 0.0743 | model |
| THIRD (Eng 6-4 Fra) | 51 | **0.0909** | 0.0981 | 0.1110 | model |
| FINAL (Esp 1-0 AET) | 49 | 0.1136 | 0.1095 | **0.1038** | market |

The model's best matches were the ones its stats were freshest for (the SFs and
THIRD came after the QF folds refined every rating); the market edged the early QFs
and the FINAL.

## Per family (Brier: raw | market — winner)

| family | N | raw | market | better |
|---|---|---|---|---|
| KXWCSCORE (exact scores) | 155 | **0.0292** | 0.0294 | model (hair) |
| KXWCTOTAL (totals) | 36 | 0.1601 | **0.1417** | market |
| KXWCMOV (moneyline) | 26 | **0.1173** | 0.1187 | model |
| KXWCSPREAD | 24 | **0.0773** | 0.0873 | model |
| KXWCFTTS (first to score) | 18 | **0.1712** | 0.1746 | model |
| KXWCGAME (3-way) | 16 | **0.2590** | 0.2747 | model |
| KXWCADVANCE | 10 | **0.2384** | 0.2502 | model |
| KXWCBTTS | 6 | **0.1820** | 0.2386 | model |
| KXMENWORLDCUP (champion) | 2 | 0.2056 | **0.1879** | market |

**Model better in 7 of 9 families.** The totals loss is real and diagnosable: the
tournament's tail games (a 6-4 THIRD) hurt a model that damps knockout scoring —
the same shape as the score-effects/dominance idea already on the backlog. The
pooled Brier is dominated by 155 low-probability exact-score rows; the per-family
view is the fairer lens, and it leans model.

## Calibration (RAW model)

| bucket | n | predicted | realized |
|---|---|---|---|
| 0–10% | 192 | 0.040 | 0.036 |
| 10–20% | 21 | 0.136 | 0.190 |
| 20–30% | 16 | 0.234 | 0.250 |
| 30–40% | 11 | 0.363 | 0.364 |
| 40–50% | 23 | 0.458 | **0.304** |
| 50–60% | 14 | 0.541 | 0.571 |
| 60–70% | 4 | 0.646 | 0.750 |
| 70–80% | 3 | 0.741 | 0.667 |
| 80–90% | 3 | 0.832 | 1.000 |
| 90–100% | 6 | 0.945 | 0.833 |

Well-behaved nearly everywhere, with one visible wobble: the 40–50% bucket
over-promised (0.458 predicted, 0.304 realized, n=23) — the model rated too many
near-coin-flips as live. For comparison, the market's own worst bucket was 50–60%
(0.551 predicted, **0.286** realized, n=14) — nobody prices coin-flips well, and
the buckets are too small to sanction. Everything above 60% is single-digit n;
read no further into it.

## The trading tests (two DIFFERENT rules — never conflate them)

The Jul 21 evaluation caught that the original replay was **not** the live KELLY
bot's rule: the replay gated on raw edge ≥5pts, while the live bot gates on
*anchored* edge ≥5pts (≈8.3pts of raw disagreement). Both now run in the
committed pipeline, flat $1 at implied + the real 0.07·P·(1−P) fee:

| rule | label | result |
|---|---|---|
| raw edge ≥5pt | **descriptive replay** (specified retrospectively) | 28 bets, 13 wins, **+3.0% ROI** |
| anchored edge ≥5pt (the live KELLY gate) | live rule, flat-staked | 17 bets, 6 wins, **−11.2% ROI** |

**The honest reading is uncomfortable and important:** the live bot's own gate,
applied evenly across all six lock-bearing matches, LOST money flat-staked.
KELLY's +45% bankroll therefore came from Kelly stake-sizing plus the two-match
window in which it happened to trade — a **pilot strategy result**, not evidence
the rule is generally profitable. Neither replay, at n=28 and n=17, supports any
edge claim on its own.

## The headline calls (advance/champion book, model's pick)

| match | call | raw | market | result |
|---|---|---|---|---|
| NOR_ENG | England advance | 0.553 | 0.680 | ✅ HIT |
| ARG_SUI | Argentina advance | 0.545 | 0.760 | ✅ HIT |
| SF1 | France advance | 0.526 | 0.570 | ❌ MISS (Spain 2-0) |
| SF2 | **Argentina advance** | **0.520** | **0.470** | ✅ **HIT — against the market** |
| THIRD | France advance | 0.529 | 0.700 | ❌ MISS (England 6-4) |
| FINAL | Spain advance | 0.547 | 0.569 | ✅ HIT |

**4 of 6** — and the texture matters more than the count:

- **SF2 is the crown jewel:** the ONLY match where model and market disagreed on the
  pick. The market leaned England (53%); the model leaned Argentina (52%).
  Argentina won. One data point, maximum satisfaction.
- **On both misses the model was much less wrong than the market.** THIRD: the
  model had France at a coin-flippish 52.9% while the market priced 70% — England
  won. SF1: model 52.6% vs market 57% on France — Spain won. When the model was
  wrong it was humble-wrong; when the market was wrong it was confident-wrong.
  (This is exactly what the Brier decomposition rewards, and why the model's
  per-family table leans green despite two missed picks.)

## Caveats, all of them

- 293 rows ≠ 293 independent samples — markets within a match are correlated; six
  matches is the real n for match-level claims.
- "Market" = Kalshi implied at T-10 (the captured book price); thin books make
  noisy quotes, and no bid/ask spread adjustment is applied to either side.
- Two QFs' locks predate the archive discipline and are absent; survivors are not
  cherry-picked (every lock that ever existed post-discipline is included).
- The pooled Brier is exact-score-heavy (155/293 rows); prefer the per-family view.
- Skill margins (+1.4%/+1.7%) are well within what luck produces at this n.

## Addendum: the sixteen-match scorecard (R16 → FINAL)

The market-level scoring above covers the six matches with frozen locks. For the
whole knockout stage, the model's **match-winner call** can be reconstructed for the
pre-lock matches: the team stats and simulator code deployed at each kickoff are in
git history, so each pairing was re-simulated with **exactly that commit's code and
stats** (raw stream, no market anchor — pre-lock market prices weren't archived).
Reconstructions are labeled; they are faithful re-runs, not tamper-proof freezes.

**Coverage honesty:** the repo's first commit landed Jul 5 02:36 UTC — *after* the
first two R16 kickoffs. CAN_MAR and PAR_FRA predate the model's existence and are
excluded. The model was born mid-round; the scorecard is the **14 matches it lived
through**.

| match | model pick (raw p) | result | call | source |
|---|---|---|---|---|
| BRA_NOR | Brazil 62.1% | **Norway 2-1** | ❌ | recon `baebb8b`¹ |
| MEX_ENG | England 52.1% | England 3-2 | ✅ | recon `baebb8b`¹ |
| POR_ESP | Spain 70.3% | Spain 1-0 | ✅ | recon `1826061` |
| USA_BEL | Belgium 60.1% | Belgium 4-1 | ✅ | recon `e4e6040` |
| ARG_EGY | Argentina 68.6% | Argentina 3-2 | ✅ | recon `f821f77` |
| SUI_COL | Switzerland **50.8%** | **0-0, pens** (SUI) | ✅ | recon `f821f77` |
| MAR_FRA | France 61.5% | France 2-0 | ✅ | recon `4243d77` |
| ESP_BEL | Spain 56.8% | Spain 2-1 | ✅ | recon `d106194` |
| NOR_ENG | England 55.3% | England 2-1 AET | ✅ | frozen lock |
| ARG_SUI | Argentina 54.5% | Argentina 3-1 AET | ✅ | frozen lock |
| SF1 | France 52.6% | **Spain 2-0** | ❌ | frozen lock |
| SF2 | Argentina 52.0% | Argentina 2-1 | ✅ | frozen lock |
| THIRD | France 52.9% | **England 6-4** | ❌ | frozen lock |
| FINAL | Spain 54.7% | Spain 1-0 AET | ✅ | frozen lock |

¹ pre-ET-era code (the extra-time simulation shipped Jul 6, 80 minutes before
POR_ESP); advance for these two = win90 + draw90/2.

**Totals: 11 of 14 winner calls (78.6%).** Advance-probability Brier **0.2097** vs
the coin-flip baseline's 0.2500 — **+16.1% skill** on 14 *independent* samples (one
per match, unlike the correlated market rows above). Reconstructed era: 7/8; frozen
era: 4/6.

Texture worth keeping:
- The only reconstruction miss is the tournament's biggest upset — Norway over
  Brazil — with the model at a moderate 62%, its most confident call being POR_ESP
  at 70.3% (hit).
- **SUI_COL is the calibration poster child:** the model said 50.8/49.2 — the match
  finished 0-0 and went to penalties. It called a coin flip a coin flip.
- Confidence on hits (57.9%) barely exceeds confidence on misses (55.9%): knockout
  football between elite sides IS close to a coin flip, and the model's honesty
  about that — against a market that priced 70% on France twice and lost both —
  is where its Brier skill lives.
- Reconstruction caveats: commit-time ≈ deploy-time is assumed (pushes were prompt;
  in the tightest case the ET-continuation commit preceded kickoff by 80 minutes);
  Monte Carlo noise on re-run probabilities is ±~1pt; raw stream only.
  Inputs + outputs archived: `research_archive/knockout_recon_2026-07-21.json`.

## Addendum 2: statistical significance (revised per the Jul 21 independent evaluation)

| test | result | verdict |
|---|---|---|
| Winner calls vs coin flip | 11/14, **one-sided p = 0.0287, two-sided p = 0.0574** | suggestive; clears 5% only one-sided |
| Advance Brier vs coin flip (bootstrap, 14 indep.) | 0.2097 vs 0.2500; skill CI brushes zero (lower bound ±0 across RNG implementations) | borderline, implementation-sensitive |
| Raw model vs market, 293 rows (6-match cluster bootstrap) | +1.4% point, 95% CI [−10.0%, +11.9%], P(better) = 59% | indistinguishable — parity |
| Anchored vs market (same bootstrap) | +1.7% point, 95% CI [−4.8%, +7.9%], P(better) = 68% | indistinguishable, leaning model |
| Expected calibration error | raw 0.0269 vs market 0.0384 under the 10-bin equal-width spec; **ordering flips under other binnings** (anchored wins equal-count specs); cluster CI for the difference [−0.017, +0.033] | **binning-sensitive, not statistically established** |
| Discrimination (AUC) | raw 0.893 / anchored 0.894 / market 0.890 | identical |

Reading, revised: the winner-call record is **suggestive** (one-sided) rather than
proven; probability quality is **parity with the executable ask** — itself a tilted
benchmark (see semantics note above); the calibration advantage is **specification-
dependent** and within cluster noise. What survives every specification: the
anchored blend never trails, the model's misses were low-confidence while the
market's were high-confidence, and the 40–50% band + totals family remain the
documented weak spots. Bootstrap seed 26, 10k resamples, both sidedness reported —
all of it emitted by `scripts/score_calibration.py` into
`calibration_results.json` and pinned by `tests/test_calibration_pipeline.py`.

## Evidence hierarchy (labeling convention, used across all V6 docs)

| label | meaning |
|---|---|
| **prospective-frozen** | recorded before the event under the T-10 procedure |
| **reconstructed** | recovered from prior source state; informative, not independently frozen |
| **descriptive-replay** | rule applied retrospectively to archived observations |
| **pilot-strategy-result** | too few independent events for strategy inference |
| **execution-comparison** | model vs executable ask/bid (spread included) |
| **forecast-comparison** | model vs midpoint/no-vig consensus (unavailable this tournament) |

Under this vocabulary: the 11/14 record is a *mixture* of prospective-frozen (6)
and reconstructed (8) evidence; the calibration tables are execution-comparisons;
the bot leaderboard is a pilot-strategy-result from a two-match window with
correlated positions; and both trading replays are descriptive.

## Reproduce it

```
.venv/bin/python scripts/score_calibration.py
```
One deterministic pipeline (seed 26 committed): joins the six archived lock
bundles to `settlements_backfill_2026-07-21.json`, recovers raw p, and emits the
FULL battery — descriptive metrics, AUC, ECE under five binnings, exact binomial
tests (both sidedness), match-cluster bootstraps, both trading replays, and the
labeled advance scorecard — to `research_archive/calibration_results.json`.
`tests/test_calibration_pipeline.py` pins every headline number, so if the
narrative and the computation ever drift, the suite fails. The raw-p recovery
identity is exact because MODEL_WEIGHT was 0.60 for every archived lock (no
mid-tournament changes). Note: the Jul 21 model-code corrections (xg_model v2,
first-goal mixture, Kelly all-in cost) change FUTURE predictions only — every
number here scores the v1 outputs that were actually frozen during the
tournament.
