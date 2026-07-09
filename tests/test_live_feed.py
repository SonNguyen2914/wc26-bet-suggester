"""Layer 2 tests: API-Football live-feed client.

No real network calls — the request layer is monkeypatched with a captured
API-Football response shape (matching the real sample: goals, status.elapsed,
events, teams). Verifies parsing, team matching, home/away orientation,
red-card detection, budget guardrails, and graceful degradation.
"""
from __future__ import annotations

import config
import src.live_feed as lf


def _fixture(home, away, gh, ga, elapsed, short="1H", events=None,
             home_id=100, away_id=200):
    return {
        "fixture": {"id": 999, "status": {"short": short, "elapsed": elapsed,
                                           "extra": None}},
        "league": {"id": 1},
        "teams": {"home": {"id": home_id, "name": home},
                  "away": {"id": away_id, "name": away}},
        "goals": {"home": gh, "away": ga},
        "events": events or [],
    }


def _patch(monkeypatch, fixtures):
    config.API_FOOTBALL_KEY = "test-key"
    lf._cache.clear()
    lf._calls_today = 0
    lf._call_date = None
    monkeypatch.setattr(lf, "_request",
                        lambda path, params: {"response": fixtures})


class TestParsingAndMatching:
    def test_basic_live_state(self, monkeypatch):
        _patch(monkeypatch, [_fixture("Brazil", "Norway", 1, 0, 70)])
        s = lf.live_state_for("Brazil", "Norway")
        assert s["home_goals"] == 1 and s["away_goals"] == 0
        assert s["minutes_elapsed"] == 70.0
        assert s["is_live"] and not s["is_finished"]

    def test_orientation_flip(self, monkeypatch):
        """API lists Norway home / Brazil away, but OUR schedule is Brazil
        home — state must be flipped to our convention."""
        _patch(monkeypatch, [_fixture("Norway", "Brazil", 2, 1, 60,
                                      home_id=200, away_id=100)])
        s = lf.live_state_for("Brazil", "Norway")
        assert s["home_goals"] == 1   # Brazil (our home) had 1
        assert s["away_goals"] == 2   # Norway (our away) had 2

    def test_accent_insensitive_match(self, monkeypatch):
        _patch(monkeypatch, [_fixture("Côte d'Ivoire", "Norway", 0, 0, 5)])
        assert lf.live_state_for("Cote dIvoire", "Norway") is not None

    def test_united_states_alias(self, monkeypatch):
        """Confirmed live 2026-07-06: our 'United States' is API-Football's
        'USA'. The alias must connect them."""
        _patch(monkeypatch, [_fixture("USA", "Belgium", 1, 2, 43)])
        s = lf.live_state_for("United States", "Belgium")
        assert s is not None
        assert s["home_goals"] == 1 and s["away_goals"] == 2

    def test_red_card_detection(self, monkeypatch):
        events = [{"type": "Card", "detail": "Red Card",
                   "team": {"id": 100}}]
        _patch(monkeypatch, [_fixture("Brazil", "Norway", 0, 0, 40,
                                      events=events)])
        s = lf.live_state_for("Brazil", "Norway")
        assert s["red_home"] == 1 and s["red_away"] == 0  # counts now

    def test_stoppage_time_added(self, monkeypatch):
        fx = _fixture("Brazil", "Norway", 1, 1, 45)
        fx["fixture"]["status"]["extra"] = 3
        _patch(monkeypatch, [fx])
        assert lf.live_state_for("Brazil", "Norway")["minutes_elapsed"] == 48.0

    def test_no_match_returns_none(self, monkeypatch):
        _patch(monkeypatch, [_fixture("Spain", "Portugal", 0, 0, 20)])
        assert lf.live_state_for("Brazil", "Norway") is None


class TestGracefulDegradation:
    def test_no_key_returns_none(self, monkeypatch):
        config.API_FOOTBALL_KEY = ""
        lf._cache.clear()
        assert lf.live_state_for("Brazil", "Norway") is None

    def test_budget_cap_blocks_calls(self, monkeypatch):
        from datetime import datetime, timezone
        config.API_FOOTBALL_KEY = "k"
        lf._cache.clear()
        lf._call_date = datetime.now(timezone.utc).date()  # today, already set
        lf._calls_today = config.API_FOOTBALL_DAILY_CAP     # already maxed
        called = {"n": 0}
        def fake_get(*a, **k):
            called["n"] += 1
            raise AssertionError("should not hit network over budget")
        monkeypatch.setattr(lf.requests, "get", fake_get)
        # real _request path (not patched) must refuse before calling out
        assert lf._request("/fixtures", {"live": "all"}) is None
        assert called["n"] == 0

    def test_budget_status_shape(self):
        b = lf.budget_status()
        assert {"calls_today", "daily_cap", "remaining",
                "key_configured"} <= set(b.keys())


class TestCaching:
    def test_second_read_is_cached(self, monkeypatch):
        config.API_FOOTBALL_KEY = "k"
        lf._cache.clear(); lf._calls_today = 0; lf._call_date = None
        calls = {"n": 0}
        def counting_request(path, params):
            calls["n"] += 1
            return {"response": [_fixture("Brazil", "Norway", 1, 0, 55)]}
        monkeypatch.setattr(lf, "_request", counting_request)
        lf.live_state_for("Brazil", "Norway")
        lf.live_state_for("Brazil", "Norway")   # within cache window
        assert calls["n"] == 1                  # only one real fetch

    def test_different_pairs_share_one_pull(self, monkeypatch):
        """Budget-drain regression: several DIFFERENT team-pairs looked up in
        one poll cycle must share a SINGLE /fixtures?live=all call, not one
        per pair (the bug that exhausted the daily budget before kickoff)."""
        config.API_FOOTBALL_KEY = "k"
        lf._cache.clear(); lf._calls_today = 0; lf._call_date = None
        calls = {"n": 0}
        def counting_request(path, params):
            calls["n"] += 1
            return {"response": [_fixture("Brazil", "Norway", 1, 0, 55)]}
        monkeypatch.setattr(lf, "_request", counting_request)
        lf.live_state_for("Brazil", "Norway")
        lf.live_state_for("Spain", "Portugal")
        lf.live_state_for("Morocco", "France")
        assert calls["n"] == 1                  # one shared pull, not three


class TestEspnFallback:
    ESPN_EVENT = {
        "id": "1", "name": "Morocco at France",
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": "2",
                 "team": {"id": "9", "displayName": "France"}},
                {"homeAway": "away", "score": "1",
                 "team": {"id": "5", "displayName": "Morocco"}},
            ],
            "details": [
                {"type": {"text": "Goal"}, "scoringPlay": True,
                 "clock": {"displayValue": "23'"},
                 "team": {"id": "9"},
                 "athletesInvolved": [{"displayName": "Kylian Mbappe"}]},
                {"type": {"text": "Red Card"}, "clock": {"displayValue": "60'"},
                 "team": {"id": "5"}},
            ],
        }],
        "status": {"type": {"state": "in", "detail": "2nd Half"},
                   "period": 2, "displayClock": "67'+2"},
    }

    def _patch_espn(self, monkeypatch, event):
        import src.live_feed as lf
        lf._cache.pop(lf._ESPN_KEY, None)
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"events": [event]}
        monkeypatch.setattr(lf.requests, "get", lambda *a, **k: R())

    def test_orientation_flip_minute_and_reds(self, monkeypatch):
        import src.live_feed as lf
        self._patch_espn(monkeypatch, self.ESPN_EVENT)
        # OUR schedule has Morocco home — ESPN lists France home -> must flip
        s = lf._espn_state_for("Morocco", "France")
        assert s is not None and s["source"] == "espn"
        assert (s["home_goals"], s["away_goals"]) == (1, 2)   # Morocco 1-2
        assert s["minutes_elapsed"] == 69.0                    # 67'+2
        assert s["red_home"] == 1 and s["red_away"] == 0       # Morocco red
        assert s["goals_list"][0]["team"] == "away"            # Mbappe -> away
        assert s["is_live"] and not s["is_finished"]

    def test_post_maps_to_finished_aet(self, monkeypatch):
        import copy
        import src.live_feed as lf
        ev = copy.deepcopy(self.ESPN_EVENT)
        ev["status"] = {"type": {"state": "post", "detail": "Final/ET"},
                        "period": 4, "displayClock": "120'"}
        self._patch_espn(monkeypatch, ev)
        s = lf._espn_state_for("Morocco", "France", want_finished=True)
        assert s["is_finished"] and s["status_short"] == "AET"

    def test_no_key_budget_paths_use_espn(self, monkeypatch):
        import src.live_feed as lf
        config.API_FOOTBALL_KEY = ""
        self._patch_espn(monkeypatch, self.ESPN_EVENT)
        s = lf.live_state_for("Morocco", "France")
        assert s is not None and s["source"] == "espn"
