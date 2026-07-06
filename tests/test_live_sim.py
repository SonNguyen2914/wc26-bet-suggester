"""Piece 1 tests: remaining-match Monte Carlo (the live in-play core).

Validated in ISOLATION before any pipeline wiring, per the project's
"validate each piece alone before the next depends on it" discipline.
Every assertion is either exact (deterministic edge cases) or bounded by
Monte Carlo noise with wide safety margins at n=10k-20k.
"""
from __future__ import annotations

import pytest

from src.models.simulator import MatchSimulator


def _stats(**over) -> dict:
    base = {"attack": 1.20, "defence": 0.90, "form": 0.60,
            "set_piece_threat": 0.20, "red_card_risk": 0.0,
            "fatigue": 0.20, "elo": 1900}
    base.update(over)
    return base


HOME = _stats(attack=1.30, defence=0.85)   # slight favorite
AWAY = _stats(attack=1.05, defence=0.95)


class TestRemainingSimCore:
    def test_t0_matches_prematch_within_mc_noise(self):
        """At minute 0, score 0-0, no cards, the live sim must reproduce the
        pre-match distribution (red_card_risk=0 makes them the same process)."""
        pre = MatchSimulator(n_simulations=20000, seed=1).simulate(HOME, AWAY)
        live = MatchSimulator(n_simulations=20000, seed=2).simulate_remaining(
            HOME, AWAY, 0, 0, minutes_elapsed=0)
        for k in ("home_win", "draw", "away_win"):
            assert abs(pre["outcomes"][k] - live["outcomes"][k]) < 0.02, k
        assert abs(pre["props"]["over_2_5"] - live["props"]["over_2_5"]) < 0.02

    def test_return_shape_matches_prematch_plus_live_state(self):
        pre = MatchSimulator(seed=3).simulate(HOME, AWAY)
        live = MatchSimulator(seed=3).simulate_remaining(HOME, AWAY, 1, 0, 70)
        assert set(pre.keys()) <= set(live.keys())
        assert set(live.keys()) - set(pre.keys()) == {"live_state"}
        ls = live["live_state"]
        assert ls["score"] == "1-0" and ls["minutes_remaining"] == 20.0
        assert ls["red_home"] is False and ls["red_away"] is False

    def test_lead_locks_in_as_clock_runs(self):
        """Same 1-0 lead is worth more at 80' than at 30'."""
        sim = MatchSimulator(n_simulations=20000, seed=4)
        early = sim.simulate_remaining(HOME, AWAY, 1, 0, 30)
        late = sim.simulate_remaining(HOME, AWAY, 1, 0, 80)
        assert late["outcomes"]["home_win"] > early["outcomes"]["home_win"] + 0.10

    def test_leading_beats_level_at_same_minute(self):
        sim = MatchSimulator(n_simulations=20000, seed=5)
        level = sim.simulate_remaining(HOME, AWAY, 0, 0, 70)
        ahead = sim.simulate_remaining(HOME, AWAY, 1, 0, 70)
        assert ahead["outcomes"]["home_win"] > level["outcomes"]["home_win"] + 0.15


class TestDeterministicEdges:
    def test_regulation_over_locks_current_score(self):
        live = MatchSimulator(seed=6).simulate_remaining(HOME, AWAY, 1, 0, 90)
        assert live["outcomes"]["home_win"] == 1.0
        assert live["outcomes"]["draw"] == 0.0
        assert live["scorelines"][0] == {"score": "1-0", "prob": 1.0}
        assert live["props"]["over_0_5"] == 1.0
        assert live["props"]["under_0_5"] == 0.0

    def test_stoppage_time_clamps_not_crashes(self):
        live = MatchSimulator(seed=7).simulate_remaining(HOME, AWAY, 2, 1, 94.5)
        assert live["outcomes"]["home_win"] == 1.0
        assert live["live_state"]["minutes_remaining"] == 0.0

    def test_current_score_floors_totals(self):
        """At 2-1 the total is already 3: every over line <= 2.5 is certain."""
        live = MatchSimulator(seed=8).simulate_remaining(HOME, AWAY, 2, 1, 60)
        assert live["props"]["over_0_5"] == 1.0
        assert live["props"]["over_1_5"] == 1.0
        assert live["props"]["over_2_5"] == 1.0
        assert live["props"]["under_2_5"] == 0.0

    def test_btts_certain_once_both_scored(self):
        live = MatchSimulator(seed=9).simulate_remaining(HOME, AWAY, 1, 1, 50)
        assert live["props"]["btts"] == 1.0

    def test_input_validation(self):
        sim = MatchSimulator(seed=10)
        with pytest.raises(ValueError):
            sim.simulate_remaining(HOME, AWAY, 0, 0, -5)
        with pytest.raises(ValueError):
            sim.simulate_remaining(HOME, AWAY, -1, 0, 10)


class TestKnownStateEffects:
    def test_red_card_hurts_carded_team(self):
        sim = MatchSimulator(n_simulations=20000, seed=11)
        clean = sim.simulate_remaining(HOME, AWAY, 0, 0, 45)
        carded = sim.simulate_remaining(HOME, AWAY, 0, 0, 45, red_home=True)
        assert carded["outcomes"]["home_win"] < clean["outcomes"]["home_win"] - 0.03
        assert carded["outcomes"]["away_win"] > clean["outcomes"]["away_win"] + 0.03
        assert carded["live_state"]["red_home"] is True

    def test_knockout_damping_lowers_remaining_lambda(self):
        sim = MatchSimulator(seed=12)
        grp = sim.simulate_remaining(HOME, AWAY, 0, 0, 45, stage="group")
        ko = sim.simulate_remaining(HOME, AWAY, 0, 0, 45, stage="knockout")
        assert ko["live_state"]["lambda_remaining"]["home"] < \
            grp["live_state"]["lambda_remaining"]["home"]

    def test_late_two_goal_deficit_means_low_advance(self):
        sim = MatchSimulator(n_simulations=20000, seed=13)
        live = sim.simulate_remaining(HOME, AWAY, 0, 2, 80, stage="knockout")
        adv = sim.prob_for_outcome_key(live, "home_advance")
        assert adv is not None and adv < 0.10


class TestOutcomeKeyCompatibility:
    """prob_for_outcome_key() must work on a live result unchanged."""

    def test_all_key_families_resolve(self):
        sim = MatchSimulator(n_simulations=20000, seed=14)
        live = sim.simulate_remaining(HOME, AWAY, 1, 0, 85, stage="knockout")
        assert sim.prob_for_outcome_key(live, "home_win") == \
            live["outcomes"]["home_win"]
        assert sim.prob_for_outcome_key(live, "over_1_5") == \
            live["props"]["over_1_5"]
        adv = sim.prob_for_outcome_key(live, "home_advance")
        assert adv == live["advance"]["home"]
        assert live["advance"]["method"] == "simulated_et_pens"
        assert adv >= live["outcomes"]["home_win"]  # continuation only adds
        # current score late should dominate the scoreline distribution
        s10 = sim.prob_for_outcome_key(live, "score_1_0")
        assert s10 is not None and s10 > 0.3

    def test_prematch_simulate_shape_unchanged(self):
        """Guards the aggregation refactor: simulate() still returns the
        exact key set the rest of the system consumes."""
        pre = MatchSimulator(seed=15).simulate(HOME, AWAY, stage="knockout")
        assert set(pre.keys()) == {"model_version", "n_simulations", "xg",
                                   "outcomes", "advance", "props",
                                   "scorelines", "confidence"}
        assert set(pre["outcomes"].keys()) == {"home_win", "draw", "away_win"}
        assert "over_2_5" in pre["props"] and "btts" in pre["props"]
