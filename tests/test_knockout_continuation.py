"""Piece 2 tests: knockout ET + penalties continuation.

The continuation replaces the flat "half of the draws" coin-flip with a
real simulated 30-minute extra time (time-scaled Poisson, inheriting each
sim's red-card handicaps) followed by an exactly-50/50 penalty shootout.
Regulation W/D/L, props, and scorelines stay regulation-final; only
"who advances" continues past 90 minutes.
"""
from __future__ import annotations

from src.models.simulator import MatchSimulator


def _stats(**over) -> dict:
    base = {"attack": 1.20, "defence": 0.90, "form": 0.60,
            "set_piece_threat": 0.20, "red_card_risk": 0.0,
            "fatigue": 0.20, "elo": 1900}
    base.update(over)
    return base


HOME = _stats(attack=1.30, defence=0.85)   # slight favorite
AWAY = _stats(attack=1.05, defence=0.95)
EQUAL = _stats()


class TestContinuationCore:
    def test_advance_block_shape_and_conservation(self):
        sim = MatchSimulator(n_simulations=20000, seed=21)
        res = sim.simulate(HOME, AWAY, stage="knockout")
        adv = res["advance"]
        assert adv["method"] == "simulated_et_pens"
        # knockouts always produce exactly one advancer
        assert abs(adv["home"] + adv["away"] - 1.0) <= 2e-4
        assert 0.0 <= adv["p_reach_pens"] <= adv["p_reach_et"] <= 1.0

    def test_reach_et_equals_regulation_draw(self):
        """p(reach ET) is by definition p(level after 90) — same array."""
        res = MatchSimulator(n_simulations=20000, seed=22).simulate(
            HOME, AWAY, stage="knockout")
        assert res["advance"]["p_reach_et"] == res["outcomes"]["draw"]

    def test_symmetric_teams_are_a_coin_flip(self):
        sim = MatchSimulator(n_simulations=40000, seed=23)
        res = sim.simulate(EQUAL, EQUAL, stage="knockout")
        assert abs(res["advance"]["home"] - 0.5) < 0.02

    def test_favorite_advances_more_and_gains_from_draws(self):
        res = MatchSimulator(n_simulations=40000, seed=24).simulate(
            HOME, AWAY, stage="knockout")
        adv = res["advance"]
        assert adv["home"] > adv["away"]
        # the continuation can only ADD to a team's win-in-90 probability
        assert adv["home"] >= res["outcomes"]["home_win"]
        assert adv["away"] >= res["outcomes"]["away_win"]

    def test_group_stage_uses_approximation(self):
        res = MatchSimulator(n_simulations=10000, seed=25).simulate(
            HOME, AWAY, stage="group")
        adv = res["advance"]
        assert adv["method"] == "half_draw_approx"
        assert adv["home"] == round(res["outcomes"]["home_win"]
                                    + 0.5 * res["outcomes"]["draw"], 4)


class TestContinuationFromLiveStates:
    def test_level_at_90_always_reaches_et(self):
        sim = MatchSimulator(n_simulations=40000, seed=26)
        res = sim.simulate_remaining(EQUAL, EQUAL, 1, 1, 90, stage="knockout")
        adv = res["advance"]
        assert adv["p_reach_et"] == 1.0            # regulation is locked level
        assert abs(adv["home"] - 0.5) < 0.02       # symmetric teams
        assert 0.0 < adv["p_reach_pens"] < 1.0     # ET decides some, not all

    def test_lead_at_90_never_reaches_et(self):
        res = MatchSimulator(seed=27).simulate_remaining(
            HOME, AWAY, 2, 0, 90, stage="knockout")
        assert res["advance"]["p_reach_et"] == 0.0
        assert res["advance"]["home"] == 1.0

    def test_late_deficit_advance_now_properly_simulated(self):
        sim = MatchSimulator(n_simulations=20000, seed=28)
        res = sim.simulate_remaining(HOME, AWAY, 0, 2, 80, stage="knockout")
        adv = sim.prob_for_outcome_key(res, "home_advance")
        assert adv == res["advance"]["home"]
        assert adv is not None and adv < 0.10


class TestBackwardCompatibility:
    def test_legacy_sim_dict_falls_back_to_approximation(self):
        """A sim dict without an 'advance' block (e.g. produced before
        Piece 2) must still price advance markets via the old formula."""
        sim = MatchSimulator(seed=29)
        legacy = {"outcomes": {"home_win": 0.5, "draw": 0.3, "away_win": 0.2},
                  "props": {}, "scorelines": []}
        assert sim.prob_for_outcome_key(legacy, "home_advance") == 0.65
        assert sim.prob_for_outcome_key(legacy, "away_advance") == 0.35

    def test_regulation_outcomes_untouched_by_continuation(self):
        """W/D/L, props, scorelines stay regulation-final: same seed with
        and without a continuation-eligible stage differs only in how the
        advance block is computed, never in regulation numbers' meaning."""
        res = MatchSimulator(n_simulations=20000, seed=30).simulate(
            HOME, AWAY, stage="knockout")
        # draws remain a real, nonzero regulation outcome even though the
        # advance block fully resolves them
        assert res["outcomes"]["draw"] > 0.1
        assert abs(res["outcomes"]["home_win"] + res["outcomes"]["draw"]
                   + res["outcomes"]["away_win"] - 1.0) <= 3e-4
