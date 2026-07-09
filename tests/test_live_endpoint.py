"""Layer 3 tests: manual live-state pricing endpoint + engine.price_live().

Uses demo mode so synthetic markets exist without a live Kalshi call.
Validates the honest-presentation contract: ephemeral, edge-ungated,
score-seeded, user levers echoed, disclaimer present.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import config
    config.DEMO_MODE = True
    from src.db import init_db
    from api.main import app
    init_db()
    return TestClient(app)


def _match_id():
    from src.schedule_data import load_schedule
    return load_schedule()[0].match_id


class TestLiveEndpointContract:
    def test_returns_live_read_and_disclaimer(self, client):
        r = client.post(f"/api/prediction/{_match_id()}/live",
                        json={"current_home": 1, "current_away": 0,
                              "minutes_elapsed": 70})
        assert r.status_code == 200
        d = r.json()
        assert d["live_state"]["score"] == "1-0"
        assert d["live_state"]["minutes_remaining"] == 20.0
        assert "disclaimer" in d and "informational" in d["disclaimer"]
        # every market row carries market vs live side by side + difference
        row = d["markets"][0]
        assert {"market_probability", "live_model_probability",
                "difference", "kalshi_odds"} <= set(row.keys())

    def test_lead_late_makes_favorite_dominant(self, client):
        mid = _match_id()
        level = client.post(f"/api/prediction/{mid}/live",
                            json={"minutes_elapsed": 80}).json()
        ahead = client.post(f"/api/prediction/{mid}/live",
                            json={"current_home": 1, "minutes_elapsed": 80}).json()
        assert ahead["live_outcomes"]["home_win"] > \
            level["live_outcomes"]["home_win"] + 0.1

    def test_attack_levers_echoed_and_applied(self, client):
        mid = _match_id()
        base = client.post(f"/api/prediction/{mid}/live",
                           json={"minutes_elapsed": 60}).json()
        pushed = client.post(f"/api/prediction/{mid}/live",
                             json={"minutes_elapsed": 60,
                                   "attack_away_mult": 1.6}).json()
        assert pushed["user_attack_levers"]["away"] == 1.6
        # away pushing harder raises their live win prob
        assert pushed["live_outcomes"]["away_win"] > \
            base["live_outcomes"]["away_win"]

    def test_red_card_flows_through(self, client):
        d = client.post(f"/api/prediction/{_match_id()}/live",
                        json={"minutes_elapsed": 45, "red_home": True}).json()
        assert d["live_state"]["red_home"] == 1  # counts; legacy bool coerces

    def test_not_persisted(self, client):
        """Live pricing must not write Predictions (ephemeral)."""
        from sqlalchemy import select, func
        from src.db import Prediction, SessionLocal
        with SessionLocal() as s:
            before = s.execute(select(func.count(Prediction.id))).scalar()
        client.post(f"/api/prediction/{_match_id()}/live",
                    json={"current_home": 2, "minutes_elapsed": 88})
        with SessionLocal() as s:
            after = s.execute(select(func.count(Prediction.id))).scalar()
        assert before == after


class TestLiveEndpointValidation:
    def test_unknown_match_404(self, client):
        assert client.post("/api/prediction/NOPE/live", json={}).status_code == 404

    def test_negative_score_422(self, client):
        r = client.post(f"/api/prediction/{_match_id()}/live",
                        json={"current_home": -1})
        assert r.status_code == 422

    def test_bad_minute_422(self, client):
        r = client.post(f"/api/prediction/{_match_id()}/live",
                        json={"minutes_elapsed": 200})
        assert r.status_code == 422

    def test_lever_out_of_range_422(self, client):
        r = client.post(f"/api/prediction/{_match_id()}/live",
                        json={"attack_home_mult": 9.0})
        assert r.status_code == 422

    def test_defaults_are_kickoff_state(self, client):
        d = client.post(f"/api/prediction/{_match_id()}/live", json={}).json()
        assert d["live_state"]["score"] == "0-0"
        assert d["live_state"]["minutes_remaining"] == 90.0
