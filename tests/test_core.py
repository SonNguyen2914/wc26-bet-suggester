"""Core sanity tests: simulator math, suggester logic, API endpoints.

Run:  pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from src.db import init_db
from src.models.simulator import MatchSimulator
from src.schedule_data import get_team_stats, load_schedule
from src.suggester import SuggesterEngine


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()


# ---------------------------------------------------------------------------
class TestSimulator:
    def setup_method(self):
        self.sim = MatchSimulator(n_simulations=20_000, seed=42)
        self.result = self.sim.simulate(get_team_stats("Brazil"), get_team_stats("Serbia"))

    def test_probabilities_sum_to_one(self):
        o = self.result["outcomes"]
        # outputs are rounded to 4dp, so allow rounding slack
        assert abs(o["home_win"] + o["draw"] + o["away_win"] - 1.0) < 1e-3

    def test_favorite_is_favored(self):
        assert self.result["outcomes"]["home_win"] > self.result["outcomes"]["away_win"]

    def test_probabilities_in_bounds(self):
        for p in {**self.result["outcomes"], **self.result["props"]}.values():
            assert 0.0 <= p <= 1.0

    def test_over_under_complementary(self):
        p = self.result["props"]
        assert abs(p["over_2_5"] + p["under_2_5"] - 1.0) < 1e-6

    def test_confidence_in_bounds(self):
        assert 0.0 < self.result["confidence"] <= 1.0

    def test_scoreline_lookup(self):
        p = self.sim.prob_for_outcome_key(self.result, "home_2_0")
        assert p is not None and 0.0 <= p <= 1.0

    def test_even_match_is_uncertain(self):
        even = self.sim.simulate(get_team_stats("Germany"), get_team_stats("Spain"))
        lopsided = self.sim.simulate(get_team_stats("Argentina"), get_team_stats("Serbia"))
        assert even["confidence"] < lopsided["confidence"]


# ---------------------------------------------------------------------------
class TestSuggester:
    def test_run_for_match_produces_suggestions(self):
        match = load_schedule()[0]
        result = SuggesterEngine().run_for_match(match, source="on_demand")
        assert len(result["suggestions"]) >= 5
        for s in result["suggestions"]:
            assert s["recommendation"] in ("TAKE", "SKIP")
            assert abs(s["edge"] - (s["model_probability"] - s["implied_probability"])) < 1e-6

    def test_suggestions_sorted_by_ev(self):
        match = load_schedule()[0]
        evs = [s["expected_value"]
               for s in SuggesterEngine().run_for_match(match)["suggestions"]]
        assert evs == sorted(evs, reverse=True)


# ---------------------------------------------------------------------------
class TestAPI:
    def setup_method(self):
        from api.main import app
        self.client = TestClient(app)

    def test_health(self):
        assert self.client.get("/api/health").json()["status"] == "ok"

    def test_upcoming(self):
        data = self.client.get("/api/matches/upcoming?hours_ahead=72").json()
        assert len(data["matches"]) >= 1

    def test_on_demand_prediction(self):
        match_id = load_schedule()[0].match_id
        data = self.client.get(f"/api/prediction/{match_id}?force_refresh=true").json()
        assert data["freshness"] == "fresh"
        assert data["age_seconds"] == 0
        assert len(data["markets"]) >= 5

    def test_cache_hit_after_fresh(self):
        match_id = load_schedule()[0].match_id
        self.client.get(f"/api/prediction/{match_id}?force_refresh=true")
        data = self.client.get(f"/api/prediction/{match_id}").json()
        assert data["freshness"] == "cached"

    def test_timeline(self):
        match_id = load_schedule()[0].match_id
        self.client.get(f"/api/prediction/{match_id}?force_refresh=true")
        data = self.client.get(f"/api/prediction/{match_id}/timeline").json()
        assert data["count"] >= 1

    def test_unknown_match_404(self):
        assert self.client.get("/api/prediction/NOT_A_MATCH").status_code == 404

    def test_settings_roundtrip(self):
        resp = self.client.post("/api/settings", json={"min_edge": 0.07}).json()
        assert resp["min_edge"] == 0.07
        self.client.post("/api/settings", json={"min_edge": 0.05})
