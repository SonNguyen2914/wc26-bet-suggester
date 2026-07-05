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
        # a red card costs the carded team ~30% of remaining xG and gifts ~15%
        lam_home = np.where(red_home, lam_home * 0.70, lam_home)
        lam_away = np.where(red_home, lam_away * 1.15, lam_away)
        lam_away = np.where(red_away, lam_away * 0.70, lam_away)
        lam_home = np.where(red_away, lam_home * 1.15, lam_home)

        goals_home = self.rng.poisson(lam_home)
        goals_away = self.rng.poisson(lam_away)

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

        return {
            "model_version": MODEL_VERSION,
            "n_simulations": self.n,
            "xg": {"home": xg_home, "away": xg_away},
            "outcomes": {
                "home_win": round(p_home, 4),
                "draw": round(p_draw, 4),
                "away_win": round(p_away, 4),
            },
            "props": props,
            "scorelines": scorelines,
            "confidence": round(float(confidence), 4),
        }

    # ------------------------------------------------------------------
    def prob_for_outcome_key(self, sim: dict, outcome_key: str) -> float | None:
        """Map a Kalshi market outcome_key to a simulated probability."""
        if outcome_key in sim["outcomes"]:
            return sim["outcomes"][outcome_key]
        if outcome_key in sim["props"]:
            return sim["props"][outcome_key]
        # Knockout "to advance" markets: win in 90 + roughly half of the
        # draws (extra time / penalties treated as a coin flip).
        if outcome_key == "home_advance":
            return round(sim["outcomes"]["home_win"] + 0.5 * sim["outcomes"]["draw"], 4)
        if outcome_key == "away_advance":
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
