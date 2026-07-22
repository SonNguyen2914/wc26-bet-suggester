"""Position tracker: verdict math, key fallbacks, flip alerts, API."""
import config
from src import positions
from src.db import SessionLocal, TrackedPosition, init_db


def setup_module(m):
    init_db()


def _wipe():
    with SessionLocal() as s:
        s.query(TrackedPosition).delete()
        s.commit()
    positions._state.clear()


class TestVerdict:
    # 100 contracts, cost $50 -> flip margin 0.05*50 = $2.50
    def test_exit_when_cashing_clearly_beats_holding(self):
        v, hold, cash = positions._verdict(0.30, 0.40, 100, 50.0)
        assert v == "EXIT" and hold == 30.0 and round(cash, 2) == 38.32

    def test_hold_when_settlement_ev_clearly_wins(self):
        v, _, _ = positions._verdict(0.50, 0.40, 100, 50.0)
        assert v == "HOLD"

    def test_close_call_inside_the_margin(self):
        v, _, _ = positions._verdict(0.40, 0.40, 100, 50.0)
        assert v == "CLOSE_CALL"


class TestEvaluate:
    def test_prefers_live_keys_falls_back_to_batch(self):
        _wipe()
        with SessionLocal() as s:
            s.add(TrackedPosition(match_id="FX", market_id="M-1",
                                  market_title="t", entry_price=0.40,
                                  contracts=100, cost=42.0))
            s.commit()
        live = {"M-1": {"live_model_probability": 0.20,
                        "market_probability": 0.50,
                        "market_yes_bid": 0.50}}
        (item,) = positions.evaluate_positions(live, "FX")
        assert item["verdict"] == "EXIT"          # cash 48.25 vs hold 20
        batch = {"M-1": {"model_probability": 0.80,
                         "market_yes_bid": 0.50}}
        (item,) = positions.evaluate_positions(batch, "FX")
        assert item["verdict"] == "HOLD"          # hold 80 vs cash 48.25
        no_bid = {"M-1": {"model_probability": 0.80,
                          "market_probability": 0.50}}
        (item,) = positions.evaluate_positions(no_bid, "FX")
        assert item["verdict"] == "NO_BID"        # ask never substitutes

    def test_alerts_fire_on_flip_into_exit_only(self, monkeypatch):
        _wipe()
        sent = []
        monkeypatch.setattr(positions, "send_alert",
                            lambda m, **k: sent.append(m))
        with SessionLocal() as s:
            s.add(TrackedPosition(match_id="FX", market_id="M-2",
                                  market_title="Spain 2-1", entry_price=0.10,
                                  contracts=400, cost=42.0))
            s.commit()
        hold_rows = {"M-2": {"live_model_probability": 0.30,
                             "market_yes_bid": 0.12}}
        exit_rows = {"M-2": {"live_model_probability": 0.02,
                             "market_yes_bid": 0.10}}
        positions.evaluate_positions(hold_rows, "FX", 10, alert=True)
        assert sent == []                          # first sighting, HOLD
        pid = next(iter(positions._state))
        positions._state[pid]["ts"] -= 999         # age past cooldown
        positions.evaluate_positions(exit_rows, "FX", 55, alert=True)
        assert len(sent) == 1 and "CASH-OUT" in sent[0]
        positions.evaluate_positions(exit_rows, "FX", 56, alert=True)
        assert len(sent) == 1                      # same verdict: silent


class TestApi:
    def test_round_trip(self):
        _wipe()
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        r = client.post("/api/positions", json={"positions": [
            {"match_id": "FINAL", "market_id": "KXWCTOTAL-X-3",
             "market_title": "Over 2.5", "entry_price": 0.42,
             "contracts": 686}]})
        assert r.status_code == 200 and r.json()["added"] == 1
        pid = r.json()["ids"][0]
        with SessionLocal() as s:
            pos = s.get(TrackedPosition, pid)
            assert pos.cost == round(686 * (0.42 + 0.07*0.42*0.58), 2)
        assert client.get("/api/positions").status_code == 200
        assert client.delete(f"/api/positions/{pid}").json()["closed"] == pid
        with SessionLocal() as s:
            assert s.get(TrackedPosition, pid).closed_at is not None
