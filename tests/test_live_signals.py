"""Watched-market BUY/SELL live signals: thresholds, cooldown/refire rules,
end-to-end persistence, and the API endpoint."""
import time

import config
import src.live_signals as sig
from src.db import (LiveSignal, MatchLiveSnapshot, SessionLocal, WatchlistItem,
                    init_db, utcnow)


def _row(model_p, market_p, market_id="M1", title="Spain to advance"):
    return {"market_id": market_id, "market_title": title,
            "live_model_probability": model_p, "market_probability": market_p}


class TestDecide:
    def test_buy_at_threshold(self):
        assert sig._decide(_row(0.62, 0.54)) == ("BUY", 0.62 - 0.54)

    def test_sell_at_threshold(self):
        side, diff = sig._decide(_row(0.40, 0.50))
        assert side == "SELL" and abs(diff - (-0.10)) < 1e-9

    def test_inside_band_is_silent(self):
        assert sig._decide(_row(0.55, 0.50)) is None

    def test_model_only_row_never_fires(self):
        # Kalshi closed the book -> nothing to buy or sell
        assert sig._decide(_row(0.90, None)) is None
        assert sig._decide(_row(None, 0.50)) is None


class TestDecideEasyWin:
    def test_easy_win_fires(self):
        side, diff = sig._decide_easy(_row(0.92, 0.80))
        assert side == "BUY" and abs(diff - 0.12) < 1e-9

    def test_not_certain_enough(self):
        assert sig._decide_easy(_row(0.80, 0.60)) is None

    def test_fully_priced_no_payout(self):
        # near-certain but the book already pays nothing
        assert sig._decide_easy(_row(0.97, 0.95)) is None

    def test_market_caught_up(self):
        # certain AND cheap enough, but gap under the min diff
        assert sig._decide_easy(_row(0.90, 0.88)) is None

    def test_model_only_row_never_fires(self):
        assert sig._decide_easy(_row(0.99, None)) is None

    def test_boundary_fires(self):
        assert sig._decide_easy(_row(0.85, 0.80)) is not None


class TestRefireRules:
    def setup_method(self):
        sig._state.clear()

    def test_first_signal_always_fires(self):
        assert sig._should_fire("M1", "BUY", 0.10)

    def test_cooldown_blocks_everything(self):
        now = time.time()
        sig._mark_fired("M1", "BUY", 0.10, now)
        # inside cooldown: same side stronger AND a side flip both stay quiet
        assert not sig._should_fire("M1", "BUY", 0.20, now + 10)
        assert not sig._should_fire("M1", "SELL", -0.20, now + 10)

    def test_side_flip_refires_after_cooldown(self):
        now = time.time()
        sig._mark_fired("M1", "BUY", 0.10, now)
        later = now + config.LIVE_SIGNAL_COOLDOWN_SECONDS + 1
        assert sig._should_fire("M1", "SELL", -0.09, later)

    def test_same_side_needs_strengthening(self):
        now = time.time()
        sig._mark_fired("M1", "BUY", 0.10, now)
        later = now + config.LIVE_SIGNAL_COOLDOWN_SECONDS + 1
        assert not sig._should_fire("M1", "BUY", 0.11, later)   # barely moved
        assert sig._should_fire("M1", "BUY", 0.10 + sig.RESTRENGTHEN, later)

    def test_markets_are_independent(self):
        sig._mark_fired("M1", "BUY", 0.10)
        assert sig._should_fire("M2", "BUY", 0.10)


class TestEvaluatePass:
    """End-to-end with a stubbed live_auto: watch a market, put the match
    live, run a pass, expect one persisted signal — then silence."""

    def setup_method(self):
        init_db()
        sig._state.clear()
        with SessionLocal() as s:
            s.query(LiveSignal).delete()
            s.query(WatchlistItem).delete()
            s.query(MatchLiveSnapshot).delete()
            s.add(WatchlistItem(match_id="SF1", market_id="KX-TEST-ESP",
                                market_title="Spain to advance"))
            s.add(MatchLiveSnapshot(match_id="SF1", home_goals=1, away_goals=0,
                                    minutes_elapsed=60.0, status_short="2H",
                                    last_seen_at=utcnow()))
            s.commit()
        self._orig = sig.live_auto
        self._calls = 0

        def fake_live_auto(match, engine, xg):
            self._calls += 1
            return {"available": True,
                    "live_state": {"minutes_elapsed": 60.0},
                    "markets": [
                        _row(0.70, 0.58, market_id="KX-TEST-ESP"),
                        _row(0.30, 0.29, market_id="KX-TEST-OTHER"),
                    ]}
        sig.live_auto = fake_live_auto

    def teardown_method(self):
        sig.live_auto = self._orig
        with SessionLocal() as s:
            s.query(LiveSignal).delete()
            s.query(WatchlistItem).delete()
            s.query(MatchLiveSnapshot).delete()
            s.commit()

    def test_pass_fires_and_persists(self):
        r = sig.evaluate_live_signals(engine=None)
        assert r["fired"] == 1 and r["checked"] == 1
        with SessionLocal() as s:
            rows = s.query(LiveSignal).all()
        assert len(rows) == 1
        row = rows[0]
        assert (row.side, row.match_id, row.market_id) == \
            ("BUY", "SF1", "KX-TEST-ESP")
        assert abs(row.difference - 0.12) < 1e-6
        assert row.minute == 60.0

    def test_second_pass_inside_cooldown_is_silent(self):
        sig.evaluate_live_signals(engine=None)
        r2 = sig.evaluate_live_signals(engine=None)
        assert r2["fired"] == 0
        with SessionLocal() as s:
            assert s.query(LiveSignal).count() == 1

    def test_no_live_snapshot_means_zero_work(self):
        with SessionLocal() as s:
            s.query(MatchLiveSnapshot).delete()
            s.commit()
        r = sig.evaluate_live_signals(engine=None)
        assert r == {"checked": 0, "fired": 0}
        assert self._calls == 0          # live_auto never invoked

    def test_unwatched_divergence_is_not_a_watched_signal(self):
        # the OTHER market diverges below the certainty bar -> neither a
        # watched signal (not watched) nor an easy win (model_p too low)
        def fake(match, engine, xg):
            return {"available": True, "live_state": {"minutes_elapsed": 60.0},
                    "markets": [_row(0.20, 0.60, market_id="KX-TEST-ESP"),
                                _row(0.70, 0.50, market_id="KX-TEST-OTHER")]}
        sig.live_auto = fake
        r = sig.evaluate_live_signals(engine=None)
        assert r["fired"] == 1
        with SessionLocal() as s:
            one = s.query(LiveSignal).one()
        assert one.side == "SELL" and one.kind == "watched"

    def test_easy_win_fires_on_unwatched_book(self):
        def fake(match, engine, xg):
            return {"available": True, "live_state": {"minutes_elapsed": 71.0},
                    "markets": [_row(0.60, 0.58, market_id="KX-TEST-ESP"),
                                _row(0.93, 0.82, market_id="KX-TEST-OVER15",
                                     title="Over 1.5 total goals")]}
        sig.live_auto = fake
        r = sig.evaluate_live_signals(engine=None)
        assert r["fired"] == 1
        with SessionLocal() as s:
            one = s.query(LiveSignal).one()
        assert (one.kind, one.side, one.market_id) == \
            ("easy_win", "BUY", "KX-TEST-OVER15")
        assert one.minute == 71.0

    def test_watched_market_excluded_from_easy_win(self):
        # watched market qualifies for BOTH scans -> only the watched
        # BUY fires, no easy-win duplicate
        def fake(match, engine, xg):
            return {"available": True, "live_state": {"minutes_elapsed": 60.0},
                    "markets": [_row(0.93, 0.82, market_id="KX-TEST-ESP")]}
        sig.live_auto = fake
        r = sig.evaluate_live_signals(engine=None)
        assert r["fired"] == 1
        with SessionLocal() as s:
            assert s.query(LiveSignal).one().kind == "watched"


class TestEndpoint:
    def setup_method(self):
        init_db()
        with SessionLocal() as s:
            s.query(LiveSignal).delete()
            s.add(LiveSignal(match_id="SF1", market_id="KX-TEST-ESP",
                             market_title="Spain to advance", side="BUY",
                             live_probability=0.70, market_probability=0.58,
                             difference=0.12, minute=60.0))
            s.commit()

    def teardown_method(self):
        with SessionLocal() as s:
            s.query(LiveSignal).delete()
            s.commit()

    def test_get_live_signals(self):
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        body = client.get("/api/live-signals").json()
        assert body["min_diff"] == config.LIVE_SIGNAL_MIN_DIFF
        assert len(body["signals"]) == 1
        srow = body["signals"][0]
        assert srow["side"] == "BUY" and srow["market_id"] == "KX-TEST-ESP"
        assert srow["minute"] == 60.0 and "fired_at" in srow

    def test_match_filter(self):
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        assert client.get("/api/live-signals",
                          params={"match_id": "SF1"}).json()["signals"]
        assert not client.get("/api/live-signals",
                              params={"match_id": "QF1"}).json()["signals"]
