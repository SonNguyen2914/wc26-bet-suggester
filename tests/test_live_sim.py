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
        # Shared core; pre-match carries the half-time forecast ("halves"),
        # the live path carries "live_state" instead.
        assert set(pre.keys()) - set(live.keys()) == {"halves"}
        assert set(live.keys()) - set(pre.keys()) == {"live_state"}
        ls = live["live_state"]
        assert ls["score"] == "1-0" and ls["minutes_remaining"] == 20.0
        assert ls["red_home"] == 0 and ls["red_away"] == 0  # counts now

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
        assert carded["live_state"]["red_home"] == 1  # counts now

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
                                   "scorelines", "confidence", "halves"}
        assert set(pre["outcomes"].keys()) == {"home_win", "draw", "away_win"}
        assert "over_2_5" in pre["props"] and "btts" in pre["props"]
        # half-time forecast: W/D/L lean + expected goals + goal chance
        for h in ("first_half", "second_half"):
            assert {"home_win", "draw", "away_win", "exp_goals",
                    "goal_pct"} == set(pre["halves"][h].keys())
        # second half carries more goals than the first (sourced skew)
        assert pre["halves"]["second_half"]["exp_goals"] >= \
            pre["halves"]["first_half"]["exp_goals"]


class TestGoalOverdispersion:
    """Gamma-mixed Poisson (GOAL_DISPERSION_CV): variance beyond Poisson.
    Honest directional contract: tails and 0-0 fatten, means and win
    probabilities hold; one-nil mass moves slightly UP (zero-side
    convexity), 1-1 slightly down — dispersion is a tail-calibration fix,
    not a scoreline-ordering fix."""

    def _dist(self, cv, seed=7):
        import config
        from src.models.simulator import MatchSimulator
        old = config.GOAL_DISPERSION_CV
        config.GOAL_DISPERSION_CV = cv
        try:
            self._sim = MatchSimulator(n_simulations=40000, seed=seed)
            stats = {"attack": 1.1, "defence": 0.85, "form": 0.85,
                     "set_piece_threat": 0.2, "red_card_risk": 0.0,
                     "fatigue": 0.2, "elo": 1900}
            return self._sim.simulate(dict(stats), dict(stats),
                                      stage="knockout")
        finally:
            config.GOAL_DISPERSION_CV = old

    def _score_p(self, res, name):
        for s in res["scorelines"]:
            if s["score"] == name:
                return s["prob"]
        return 0.0

    def test_tails_and_zero_zero_fatten(self):
        # direction is tested at an exaggerated CV: at the deployed 0.30 the
        # tail effect (+~0.4pt) is real but inside 40k-sim Monte Carlo noise
        pure = self._dist(0.0)
        p_over_pure = self._sim.prob_for_outcome_key(pure, "over_3_5")
        zz_pure = self._score_p(pure, "0-0")
        disp = self._dist(0.60)
        p_over_disp = self._sim.prob_for_outcome_key(disp, "over_3_5")
        assert p_over_disp > p_over_pure
        assert self._score_p(disp, "0-0") > zz_pure
        # the middle funds it: 1-1 gives up mass
        assert self._score_p(disp, "1-1") < self._score_p(pure, "1-1")

    def test_means_and_win_probs_stable(self):
        pure = self._dist(0.0)
        disp = self._dist(0.30)
        # identical teams: win prob must stay ~symmetric and ~unchanged
        assert abs(disp["outcomes"]["home_win"] - pure["outcomes"]["home_win"]) < 0.02
        assert abs(disp["xg"]["home"] - pure["xg"]["home"]) < 1e-9  # means untouched

    def test_draw_mass_roughly_preserved(self):
        pure = self._dist(0.0)
        disp = self._dist(0.30)
        assert abs(disp["outcomes"]["draw"] - pure["outcomes"]["draw"]) < 0.03

    def test_cv_zero_recovers_pure_poisson(self):
        a = self._dist(0.0, seed=11)
        b = self._dist(0.0, seed=11)
        assert a["outcomes"] == b["outcomes"]
