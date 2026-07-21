# Model vs Market — the Calibration Write-up

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
| **MARKET** | Kalshi's implied probability at T-10 |

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

## The trading test (the KELLY rule, replayed on locks)

Flat $1 on every lock where the RAW model showed ≥5pt edge at a price in
[10¢, 90¢], bought at implied + the real 0.07·P·(1−P) fee:

> **28 bets, 13 wins, +$0.85 → +3.0% ROI after fees.**

Modest, positive, and consistent with the live arena (KELLY's bankroll, staked
half-Kelly rather than flat, finished +45% on the same idea). Same caveat as ever:
n=28.

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

## Reproduce it

```
.venv/bin/python scripts/score_calibration.py
```
Joins the six archived lock bundles to `settlements_backfill_2026-07-21.json`,
recovers raw p, scores all three streams, prints every table above, and dumps the
per-row join to `research_archive/calibration_scored_rows.json`. The recovery
identity is exact because MODEL_WEIGHT was 0.60 for every archived lock (no
mid-tournament changes).
