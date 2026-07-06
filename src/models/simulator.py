"""Monte Carlo match simulator.

Runs N Poisson-sampled matches from the xG model, folds in red-card risk,
and returns probabilities for every market type we track plus an
ensemble-style confidence estimate.
"""
from __future__ import annotations

import numpy as np

import config
from src.models.features import stage_uncertainty
from src.models.xg_model import MODEL_VERSION, predict_xg

# --- Knockout continuation (Piece 2) ---------------------------------------
# Extra time = 30 more minutes of the same Poisson process, so each team's ET
# rate is its effective per-90 rate x (30/90). Real ET scoring behavior may
# differ (tired legs, caution), but quantifying that would be a hand-guess;
# linear time-scaling is the guess-free default (Poisson is memoryless).
ET_MINUTES = 30
# Penalty shootouts are empirically near coin flips — only mildly
# skill-influenced. Any tilt we invented would be an ungrounded number,
# so the continuation resolves unbroken ties at exactly 50/50.
PENALTY_HOME_WIN_P = 0.5

# --- Red-card effect (Piece 3) ----------------------------------------------
# Vecer, Kopriva & Ichiba (2009), "Estimating the Effect of the Red Card in
# Soccer", J. Quantitative Analysis in Sports 5(1): estimated from in-play
# betting data on FIFA World Cup 2006 + Euro 2008 (same competition class as
# ours). The sanctioned team's scoring intensity falls to ~2/3 of its
# original rate; the opponent's rises by a factor of ~5/4. Direction
# corroborated by van Ours & van Tuijl (2017, Empirical Economics) on WC
# matches 1998-2014. Replaces the previous hand-guessed 0.70 / 1.15.
RED_CARD_OWN_MULT = 0.67   # carded team's rate: x ~2/3
RED_CARD_OPP_MULT = 1.25   # opponent's rate:    x ~5/4


class MatchSimulator:
    def __init__(self, n_simulations: int | None = None, seed: int | None = None):
        self.n = n_simulations or config.N_SIMULATIONS
        self.rng = np.random.default_rng(seed)

    def simulate(self, home_raw: dict, away_raw: dict, stage: str = "group") -> dict:
        xg_home, xg_away = predict_xg(home_raw, away_raw)

        # --- Red card adjustment: sample card events, penalize the carded side.
        p_red_home = home_raw.get("red_card_risk", 0.06)
        p_red_away = away_raw.get("red_card_risk", 0.06)
        red_home = self.rng.random(self.n) < p_red_home
        red_away = self.rng.random(self.n) < p_red_away

        lam_home = np.full(self.n, xg_home)
        lam_away = np.full(self.n, xg_away)
        # Knockout football is cagier than raw team ratings imply: teams
        # protect leads and avoid risks with elimination on the line.
        # Historical WC knockout matches average ~15% fewer goals than an
        # equivalent group fixture, so damp both lambdas accordingly.
        if stage == "knockout":
            lam_home *= 0.85
            lam_away *= 0.85
        # Red card: sourced coefficients (see RED_CARD_* constants above)
        lam_home = np.where(red_home, lam_home * RED_CARD_OWN_MULT, lam_home)
        lam_away = np.where(red_home, lam_away * RED_CARD_OPP_MULT, lam_away)
        lam_away = np.where(red_away, lam_away * RED_CARD_OWN_MULT, lam_away)
        lam_home = np.where(red_away, lam_home * RED_CARD_OPP_MULT, lam_home)

        goals_home = self.rng.poisson(lam_home)
        goals_away = self.rng.poisson(lam_away)

        return self._aggregate_outcomes(goals_home, goals_away, stage,
                                        xg_home, xg_away,
                                        lam90_home=lam_home,
                                        lam90_away=lam_away)

    # ------------------------------------------------------------------
    def _aggregate_outcomes(self, goals_home: np.ndarray, goals_away: np.ndarray,
                            stage: str, xg_home: float, xg_away: float,
                            lam90_home=None, lam90_away=None) -> dict:
        """Shared tail of every simulation: per-sim FINAL scores in, the
        outcome/props/scorelines/confidence dict out.

        Used by both the pre-match simulate() and the live
        simulate_remaining() so their return shapes structurally cannot
        drift apart — prob_for_outcome_key() works on either result.

        lam90_home/lam90_away are each team's effective per-90 goal rate
        (scalar, or a per-sim array so red-card handicaps carry into the
        continuation). When provided for a knockout, level regulations
        continue into a simulated ET + penalties (Piece 2); otherwise
        advancement falls back to the half-the-draws approximation.
        """
        # --- Aggregate outcomes
        p_home = float(np.mean(goals_home > goals_away))
        p_draw = float(np.mean(goals_home == goals_away))
        p_away = float(np.mean(goals_home < goals_away))

        total = goals_home + goals_away
        p_btts = float(np.mean((goals_home > 0) & (goals_away > 0)))
        margin = goals_home - goals_away

        props = {"btts": round(p_btts, 4)}
        # Full totals ladder (matches Kalshi's KXWCTOTAL markets, over 0.5-5.5)
        for line in range(6):
            p_over = float(np.mean(total > line + 0.5))
            props[f"over_{line}_5"] = round(p_over, 4)
            props[f"under_{line}_5"] = round(1 - p_over, 4)
        # Winning margins (matches Kalshi's KXWCSPREAD markets, "wins by 1.5+/2.5+")
        for m_line in (2, 3):
            props[f"home_margin_{m_line}"] = round(float(np.mean(margin >= m_line)), 4)
            props[f"away_margin_{m_line}"] = round(float(np.mean(-margin >= m_line)), 4)

        # --- Scoreline distribution (top 10)
        pairs, counts = np.unique(
            np.stack([goals_home, goals_away], axis=1), axis=0, return_counts=True
        )
        order = np.argsort(-counts)
        scorelines = [
            {"score": f"{int(h)}-{int(a)}", "prob": round(float(c) / self.n, 4)}
            for (h, a), c in zip(pairs[order][:10], counts[order][:10])
        ]

        # --- Confidence: how decisive is the distribution?
        # Low entropy over {home, draw, away} + tight scoreline mass = confident.
        probs = np.clip(np.array([p_home, p_draw, p_away]), 1e-9, 1)
        entropy = float(-(probs * np.log(probs)).sum())          # 0 .. ln(3)
        confidence = 1.0 - entropy / np.log(3)                    # 0 .. 1
        confidence = 0.5 + 0.5 * confidence                       # floor at 0.5
        confidence /= stage_uncertainty(stage)                    # knockout haircut

        # --- Knockout advancement (Piece 2): real ET + penalties continuation.
        # Regulation W/D/L, props, and scorelines above stay REGULATION-final
        # (that's what the Kalshi market families settle on); only "who
        # advances" continues past 90 minutes.
        if stage == "knockout" and lam90_home is not None and lam90_away is not None:
            level = goals_home == goals_away
            et_scale = ET_MINUTES / 90.0
            et_home = self.rng.poisson(np.asarray(lam90_home) * et_scale, self.n)
            et_away = self.rng.poisson(np.asarray(lam90_away) * et_scale, self.n)
            still_level = level & (et_home == et_away)
            pens_home = self.rng.random(self.n) < PENALTY_HOME_WIN_P
            home_adv = (goals_home > goals_away) \
                | (level & (et_home > et_away)) \
                | (still_level & pens_home)
            advance = {
                "home": round(float(np.mean(home_adv)), 4),
                "away": round(float(np.mean(~home_adv)), 4),
                "p_reach_et": round(float(np.mean(level)), 4),
                "p_reach_pens": round(float(np.mean(still_level)), 4),
                "method": "simulated_et_pens",
            }
        else:
            advance = {
                "home": round(p_home + 0.5 * p_draw, 4),
                "away": round(p_away + 0.5 * p_draw, 4),
                "p_reach_et": round(p_draw, 4),
                "p_reach_pens": None,
                "method": "half_draw_approx",
            }

        return {
            "model_version": MODEL_VERSION,
            "n_simulations": self.n,
            "xg": {"home": xg_home, "away": xg_away},
            "outcomes": {
                "home_win": round(p_home, 4),
                "draw": round(p_draw, 4),
                "away_win": round(p_away, 4),
            },
            "advance": advance,
            "props": props,
            "scorelines": scorelines,
            "confidence": round(float(confidence), 4),
        }

    # ------------------------------------------------------------------
    def simulate_remaining(self, home_raw: dict, away_raw: dict,
                           current_home: int, current_away: int,
                           minutes_elapsed: float, stage: str = "group",
                           red_home: bool = False, red_away: bool = False) -> dict:
        """Live in-play core (Piece 1): simulate only the REMAINDER of a
        match from a known state, seeded with the current score.

        Answers "who wins from HERE?" instead of the pre-match "who wins
        from 0-0?". Every input is checkable state, not a guess:
          - Goal rates come from the same xG model, time-scaled by
            (90 - minutes_elapsed) / 90. A Poisson process is memoryless,
            so the remaining-interval rate scales linearly with time left.
          - The current score seeds every simulation; remaining sampled
            goals are added on top. Totals/BTTS/scorelines therefore
            reflect FINAL totals automatically (e.g. at 1-0, over_0_5 is
            exactly 1.0 and btts is exactly P(away scores in remainder)).
          - Red cards are KNOWN boolean inputs (they happened or they
            didn't), not sampled risks. Coefficients are literature-sourced
            (Vecer et al. 2009, WC 2006 + Euro 2008 in-play data: carded
            side x~2/3, opponent x~5/4 — see RED_CARD_* constants).

        v1 limitations (documented, deliberate):
          - minutes_elapsed >= 90 treats regulation as complete (stoppage
            time is not modeled); the current score is returned as final.
          - Knockout advancement DOES include the Piece-2 continuation
            (simulated 30-min ET at time-scaled rates, then 50/50
            penalties — see the "advance" block). What is still NOT
            representable is a live state *inside* extra time (a minute
            past 90 clamps to "regulation over").
          - The knockout x0.85 goal damping is inherited from the
            pre-match model (known hand-tuned debt) for consistency.

        Returns the exact same shape as simulate(), plus a "live_state"
        block, so prob_for_outcome_key() works on either result unchanged.
        """
        if minutes_elapsed < 0:
            raise ValueError("minutes_elapsed cannot be negative")
        if current_home < 0 or current_away < 0:
            raise ValueError("current score cannot be negative")

        xg_home, xg_away = predict_xg(home_raw, away_raw)
        frac_remaining = max(0.0, (90.0 - float(minutes_elapsed)) / 90.0)

        # Effective per-90 rates: damping + KNOWN cards applied BEFORE time
        # scaling — the ET continuation reuses these at 30/90.
        rate_home, rate_away = xg_home, xg_away
        if stage == "knockout":          # same damping as pre-match (debt)
            rate_home *= 0.85
            rate_away *= 0.85
        if red_home:                     # known state, applies to remainder
            rate_home *= RED_CARD_OWN_MULT
            rate_away *= RED_CARD_OPP_MULT
        if red_away:
            rate_away *= RED_CARD_OWN_MULT
            rate_home *= RED_CARD_OPP_MULT
        lam_home = rate_home * frac_remaining
        lam_away = rate_away * frac_remaining

        rem_home = self.rng.poisson(lam_home, self.n)
        rem_away = self.rng.poisson(lam_away, self.n)
        goals_home = current_home + rem_home
        goals_away = current_away + rem_away

        result = self._aggregate_outcomes(goals_home, goals_away, stage,
                                          xg_home, xg_away,
                                          lam90_home=rate_home,
                                          lam90_away=rate_away)
        result["live_state"] = {
            "score": f"{current_home}-{current_away}",
            "minutes_elapsed": round(float(minutes_elapsed), 1),
            "minutes_remaining": round(90.0 * frac_remaining, 1),
            "red_home": bool(red_home),
            "red_away": bool(red_away),
            "lambda_remaining": {"home": round(lam_home, 3),
                                 "away": round(lam_away, 3)},
        }
        return result

    # ------------------------------------------------------------------
    def prob_for_outcome_key(self, sim: dict, outcome_key: str) -> float | None:
        """Map a Kalshi market outcome_key to a simulated probability."""
        if outcome_key in sim["outcomes"]:
            return sim["outcomes"][outcome_key]
        if outcome_key in sim["props"]:
            return sim["props"][outcome_key]
        # Knockout "to advance" markets. Piece 2: prefer the simulated
        # ET + penalties continuation when present; fall back to the old
        # half-the-draws coin-flip approximation for legacy sim dicts.
        if outcome_key == "home_advance":
            adv = sim.get("advance")
            if adv is not None:
                return adv["home"]
            return round(sim["outcomes"]["home_win"] + 0.5 * sim["outcomes"]["draw"], 4)
        if outcome_key == "away_advance":
            adv = sim.get("advance")
            if adv is not None:
                return adv["away"]
            return round(sim["outcomes"]["away_win"] + 0.5 * sim["outcomes"]["draw"], 4)
        # Exact final scores: score_2_0 → "2-0" (from Kalshi KXWCSCORE).
        # Only priced when the scoreline appears in our top-10 distribution;
        # rarer scores return None and get skipped rather than mispriced.
        if outcome_key.startswith("score_"):
            parts = outcome_key.split("_")
            if len(parts) == 3:
                target = f"{parts[1]}-{parts[2]}"
                for s in sim["scorelines"]:
                    if s["score"] == target:
                        return s["prob"]
            return None
        # exact scorelines legacy demo keys: home_2_0 -> "2-0"
        if outcome_key.startswith(("home_", "away_")) and outcome_key.count("_") == 2:
            _, h, a = outcome_key.split("_")
            target = f"{h}-{a}"
            for s in sim["scorelines"]:
                if s["score"] == target:
                    return s["prob"]
            return 0.0
        return None
