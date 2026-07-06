"""Piece 3 tests: red-card coefficients are literature-sourced and applied.

Source: Vecer, Kopriva & Ichiba (2009), JQAS 5(1) — WC 2006 + Euro 2008
in-play betting data: carded team x~2/3, opponent x~5/4.
"""
from src.models.simulator import (MatchSimulator, RED_CARD_OWN_MULT,
                                  RED_CARD_OPP_MULT)


def _stats(**over):
    base = {"attack": 1.20, "defence": 0.90, "form": 0.60,
            "set_piece_threat": 0.20, "red_card_risk": 0.0,
            "fatigue": 0.20, "elo": 1900}
    base.update(over)
    return base


class TestSourcedCoefficients:
    def test_constants_match_literature(self):
        assert RED_CARD_OWN_MULT == 0.67   # ~2/3 (Vecer et al. 2009)
        assert RED_CARD_OPP_MULT == 1.25   # ~5/4 (Vecer et al. 2009)

    def test_live_path_applies_exact_multipliers(self):
        """lambda_remaining must reflect the constants exactly (minute 0,
        so frac=1 and the rate IS the per-90 rate)."""
        h, a = _stats(), _stats()
        sim = MatchSimulator(n_simulations=1000, seed=31)
        clean = sim.simulate_remaining(h, a, 0, 0, 0)["live_state"]["lambda_remaining"]
        red = sim.simulate_remaining(h, a, 0, 0, 0, red_home=True)["live_state"]["lambda_remaining"]
        assert abs(red["home"] / clean["home"] - RED_CARD_OWN_MULT) < 0.01
        assert abs(red["away"] / clean["away"] - RED_CARD_OPP_MULT) < 0.01

    def test_prematch_sampled_card_hurts(self):
        """Pre-match: certain red for home (risk=1.0) vs none — the sourced
        effect must show in outcomes."""
        sim = MatchSimulator(n_simulations=20000, seed=32)
        clean = sim.simulate(_stats(), _stats())
        carded = sim.simulate(_stats(red_card_risk=1.0), _stats())
        assert carded["outcomes"]["home_win"] < clean["outcomes"]["home_win"] - 0.05
        assert carded["outcomes"]["away_win"] > clean["outcomes"]["away_win"] + 0.05
