"""Tests for the bet-timing (ripeness) system.

Run:  pytest tests/test_timing.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import config
from src.db import init_db, SessionLocal, OddsReading, WatchlistItem, TimingAlert
from src.schedule_data import load_schedule
from src.timing import compute_timing, record_reading, should_alert


@pytest.fixture(scope="session", autouse=True)
def _db():
    init_db()


def _clear(market_id: str):
    with SessionLocal() as s:
        s.query(OddsReading).filter_by(market_id=market_id).delete()
        s.query(TimingAlert).filter_by(market_id=market_id).delete()
        s.commit()


def _feed(match_id: str, market_id: str, series: list[tuple[float, float]]):
    """series: list of (yes_price, model_probability)."""
    for yes, model_p in series:
        record_reading(match_id, {
            "market_id": market_id, "yes_price": yes,
            "decimal_odds": round(1 / yes, 2), "volume_24h": 50_000,
        }, model_p)


class TestTiming:
    MKT = "TEST-MKT-RIPENESS"

    def test_no_data_scores_zero(self):
        _clear("TEST-EMPTY")
        match = load_schedule()[0]
        t = compute_timing("TEST-EMPTY", match.kickoff)
        assert t["score"] == 0 and t["status"] == "no_data"

    def test_provisional_damping(self):
        _clear(self.MKT)
        match = load_schedule()[0]
        _feed(match.match_id, self.MKT, [(0.50, 0.55)] * 5)  # < MIN_READINGS
        t = compute_timing(self.MKT, match.kickoff)
        assert t["status"] == "provisional"

    def test_exceptional_edge_scores_high(self):
        _clear(self.MKT)
        match = load_schedule()[0]  # kicks off in ~3h → high urgency
        # 20 boring readings (edge ~+1%), then price collapses: edge jumps to +15%
        series = [(0.50, 0.51)] * 20 + [(0.40, 0.55), (0.38, 0.55), (0.36, 0.55)]
        _feed(match.match_id, self.MKT, series)
        t = compute_timing(self.MKT, match.kickoff)
        assert t["status"] == "learned"
        assert t["components"]["z"] > 0.8          # edge is extreme vs baseline
        assert t["components"]["percentile"] > 0.9  # best price seen
        assert t["score"] > 60

    def test_flat_market_scores_low(self):
        _clear(self.MKT)
        match = load_schedule()[-1]  # kicks off in ~50h → low urgency
        _feed(match.match_id, self.MKT, [(0.50, 0.50)] * 25)  # zero edge, flat
        t = compute_timing(self.MKT, match.kickoff)
        assert t["score"] < config.RIPENESS_ALERT_THRESHOLD

    def test_should_alert_requires_positive_edge(self):
        # High score but negative edge must never alert
        fake = {"score": 99, "current_edge": -0.02, "current_odds": 2.0, "reasons": []}
        assert should_alert("TEST-NEG-EDGE", fake) is False

    def test_alert_cooldown(self):
        from src.timing import save_alert
        _clear(self.MKT)
        timing = {"score": 90, "current_edge": 0.10, "current_odds": 2.5,
                  "reasons": ["test"]}
        assert should_alert(self.MKT, timing) is True
        save_alert("BRA_SRB", self.MKT, "test market", timing)
        assert should_alert(self.MKT, timing) is False  # cooldown active


class TestWatchlistAPI:
    def setup_method(self):
        from api.main import app
        self.client = TestClient(app)
        with SessionLocal() as s:
            s.query(WatchlistItem).delete()
            s.commit()

    def test_add_list_remove(self):
        body = {"match_id": "BRA_SRB", "market_id": "WC26-BRA_SRB-HOME_WIN",
                "market_title": "Brazil to win"}
        assert self.client.post("/api/watchlist", json=body).json()["status"] == "watching"
        assert self.client.post("/api/watchlist", json=body).json()["status"] == "already_watching"

        wl = self.client.get("/api/watchlist").json()
        assert len(wl["watchlist"]) == 1
        assert "timing" in wl["watchlist"][0]

        assert self.client.delete(
            "/api/watchlist/WC26-BRA_SRB-HOME_WIN").json()["status"] == "removed"
        assert self.client.get("/api/watchlist").json()["watchlist"] == []

    def test_unknown_match_rejected(self):
        body = {"match_id": "FAKE", "market_id": "X"}
        assert self.client.post("/api/watchlist", json=body).status_code == 404

    def test_timing_endpoint(self):
        r = self.client.get("/api/timing/BRA_SRB/WC26-BRA_SRB-HOME_WIN")
        assert r.status_code == 200
        assert "score" in r.json()

    def test_alerts_feed(self):
        r = self.client.get("/api/alerts/recent")
        assert r.status_code == 200
        assert "alerts" in r.json()
