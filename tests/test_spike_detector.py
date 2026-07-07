"""Layer 1 tests: scoreline spike detection (log-only).

The detector must fire on a genuine more-goals re-center of the scoreline
distribution and STAY QUIET on noise, ties, lower-goal shifts, and repeats
inside the cooldown. It must never touch predictions — it only returns an
event dict / prints.
"""
from __future__ import annotations

import src.spike_detector as sd


def _mkts(dist: dict[tuple[int, int], float]) -> list[dict]:
    """Build score_* markets from a {(h,a): yes_price} dict."""
    return [{"outcome_key": f"score_{h}_{a}", "yes_price": p,
             "market_title": f"{h}-{a}"} for (h, a), p in dist.items()]


def setup_function():
    sd.reset()


class TestFiresOnGoal:
    def test_recenter_to_more_goals_flags(self):
        t = 1000.0
        # start: 0-0 clearly leads
        assert sd.inspect("M", _mkts({(0, 0): 0.55, (1, 0): 0.15,
                                      (0, 1): 0.15}), now=t) is None
        # a goal: 1-0 now leads by a clear margin, higher total
        ev = sd.inspect("M", _mkts({(0, 0): 0.10, (1, 0): 0.45,
                                    (0, 1): 0.12}), now=t + 5)
        assert ev is not None
        assert ev["suspected_score"] == "1-0"
        assert ev["previous_score"] == "0-0"
        assert ev["goals_delta"] == 1

    def test_second_goal_also_flags_after_cooldown(self):
        t = 2000.0
        sd.inspect("M", _mkts({(0, 0): 0.55, (1, 0): 0.15}), now=t)
        sd.inspect("M", _mkts({(1, 0): 0.5, (0, 0): 0.1}), now=t + 5)  # 1-0
        # well past cooldown, 2-0 takes the lead
        ev = sd.inspect("M", _mkts({(2, 0): 0.5, (1, 0): 0.12}),
                        now=t + 5 + sd.COOLDOWN_AFTER_FLAG + 1)
        assert ev is not None and ev["suspected_score"] == "2-0"


class TestStaysQuiet:
    def test_no_leader_change_is_silent(self):
        t = 3000.0
        sd.inspect("M", _mkts({(0, 0): 0.55, (1, 0): 0.15}), now=t)
        assert sd.inspect("M", _mkts({(0, 0): 0.5, (1, 0): 0.2}),
                          now=t + 5) is None

    def test_lower_total_shift_ignored(self):
        """If the leader somehow moves to a FEWER-goals score, that's not a
        goal — never flag it."""
        t = 4000.0
        sd.inspect("M", _mkts({(1, 1): 0.5, (0, 0): 0.2}), now=t)  # lead 1-1
        assert sd.inspect("M", _mkts({(0, 0): 0.5, (1, 1): 0.2}),
                          now=t + 5) is None  # 'moved' to 0-0: impossible, ignore

    def test_small_margin_is_noise(self):
        t = 5000.0
        sd.inspect("M", _mkts({(0, 0): 0.40, (1, 0): 0.38}), now=t)
        # 1-0 edges ahead but only by a hair — below MIN_MARGIN vs old leader
        assert sd.inspect("M", _mkts({(1, 0): 0.41, (0, 0): 0.39}),
                          now=t + 5) is None

    def test_low_confidence_leader_ignored(self):
        t = 6000.0
        sd.inspect("M", _mkts({(0, 0): 0.30, (1, 0): 0.10}), now=t)
        # thin market: new leader below MIN_LEADER_PRICE
        assert sd.inspect("M", _mkts({(1, 0): 0.12, (0, 0): 0.10,
                                      (2, 1): 0.11}), now=t + 5) is None

    def test_debounce_blocks_rapid_reflag(self):
        t = 7000.0
        sd.inspect("M", _mkts({(0, 0): 0.55, (1, 0): 0.15}), now=t)
        ev1 = sd.inspect("M", _mkts({(1, 0): 0.5, (0, 0): 0.1}), now=t + 5)
        assert ev1 is not None
        # 2-0 leads almost immediately — inside cooldown, must not flag
        ev2 = sd.inspect("M", _mkts({(2, 0): 0.5, (1, 0): 0.1}), now=t + 10)
        assert ev2 is None


class TestIsolation:
    def test_no_score_markets_returns_none(self):
        assert sd.inspect("M", [{"outcome_key": "home_win",
                                 "yes_price": 0.5}]) is None

    def test_per_match_state_is_separate(self):
        t = 8000.0
        sd.inspect("A", _mkts({(0, 0): 0.55, (1, 0): 0.15}), now=t)
        sd.inspect("B", _mkts({(0, 0): 0.55, (1, 0): 0.15}), now=t)
        ev = sd.inspect("A", _mkts({(1, 0): 0.5, (0, 0): 0.1}), now=t + 5)
        assert ev is not None                       # A flagged
        assert sd.current_leader("B") == (0, 0)     # B untouched
