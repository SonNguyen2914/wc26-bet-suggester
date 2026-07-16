"""Strategy-lab bots: entry rules, sizing, position lifecycle, settlement."""
import json

from src import bots
from src.db import (BotPosition, MarketClosing, SessionLocal, init_db, utcnow)


def _row(model_p, implied, market_id="M1", title="Spain to win"):
    return {"market_id": market_id, "market_title": title,
            "model_probability": model_p, "implied_probability": implied}


def _live(live_p, market_p, market_id="M1", title="Spain to win"):
    return {"market_id": market_id, "market_title": title,
            "live_model_probability": live_p, "market_probability": market_p}


class TestEntryRules:
    def test_kelly_needs_real_edge(self):
        assert bots.kelly_entries([_row(0.62, 0.54)], 1000)      # 8pt edge
        assert not bots.kelly_entries([_row(0.57, 0.54)], 1000)  # 3pt
        assert not bots.kelly_entries([_row(0.97, 0.93)], 1000)  # >90c band
        # stake: half-kelly, capped at $150
        (_, _, _, stake, _), = bots.kelly_entries([_row(0.70, 0.50)], 1000)
        assert stake == 150.0     # f*=0.4, half=0.2 -> $200 -> cap

    def test_chalk_wants_favourites_that_still_pay(self):
        assert bots.chalk_entries([_row(0.70, 0.60)], 1000)
        assert not bots.chalk_entries([_row(0.60, 0.55)], 1000)   # not 65%
        assert not bots.chalk_entries([_row(0.92, 0.90)], 1000)   # no payout
        (_, _, _, stake, _), = bots.chalk_entries([_row(0.70, 0.60)], 1000)
        assert stake == 50.0

    def test_moonshot_wants_mispriced_longshots(self):
        assert bots.moonshot_entries([_row(0.15, 0.10)], 1000)    # 1.5x
        assert not bots.moonshot_entries([_row(0.12, 0.10)], 1000)  # 1.2x
        assert not bots.moonshot_entries([_row(0.40, 0.28)], 1000)  # too dear
        assert not bots.moonshot_entries([_row(0.02, 0.01)], 1000)  # dust

    def test_wire_follows_buy_signals_only(self):
        sigs = [{"market_id": "A", "market_title": "t", "side": "BUY",
                 "kind": "easy_win", "market_probability": 0.8, "minute": 70},
                {"market_id": "B", "market_title": "t", "side": "SELL",
                 "kind": "watched", "market_probability": 0.4, "minute": 70}]
        entries = bots.wire_entries(sigs, 1000)
        assert [e[0] for e in entries] == ["A"]

    def test_fade_buys_the_crash_the_model_disputes(self):
        refs = {"M1": 0.60}
        # crashed 22c, live model 8pts over the new price -> buy
        assert bots.fade_entries([_live(0.50, 0.38)], refs, 1000)
        # crashed but the model agrees with the crash -> no
        assert not bots.fade_entries([_live(0.40, 0.38)], refs, 1000)
        # model likes it but no crash -> no
        assert not bots.fade_entries([_live(0.70, 0.55)], refs, 1000)


class TestPositionLifecycle:
    def setup_method(self):
        init_db()
        with SessionLocal() as s:
            s.query(BotPosition).delete()
            s.query(MarketClosing).delete()
            s.commit()

    teardown_method = setup_method

    def test_open_and_double_entry_blocked(self):
        r = bots.open_position("KELLY", "FINAL", "KX-1", "t", 0.50, 100.0)
        assert r is not None
        # unit = 0.5 + fee(0.5)=0.0175 -> 0.5175; 100//0.5175 = 193 contracts
        with SessionLocal() as s:
            pos = s.query(BotPosition).one()
        assert pos.contracts == int(100 // (0.5 + bots.fee(0.5)))
        assert bots.open_position("KELLY", "FINAL", "KX-1", "t", 0.45, 50.0) is None

    def test_bankroll_decreases_with_open_cost(self):
        bots.open_position("CHALK", "FINAL", "KX-2", "t", 0.60, 50.0)
        with SessionLocal() as s:
            assert bots.bankroll("CHALK", s) < bots.START_BANKROLL
            assert bots.bankroll("KELLY", s) == bots.START_BANKROLL

    def test_early_exit_math(self):
        bots.open_position("WIRE", "FINAL", "KX-3", "t", 0.50, 40.0)
        with SessionLocal() as s:
            pos = s.query(BotPosition).one()
        bots.close_position(pos.id, 0.75, "take profit +20c")
        with SessionLocal() as s:
            pos = s.query(BotPosition).one()
            assert pos.close_reason == "take profit +20c"
            expect = pos.contracts * (0.75 - bots.fee(0.75))
            assert abs(pos.pnl - expect) < 0.01
            assert pos.pnl > pos.cost          # profitable trade
            assert bots.bankroll("WIRE", s) > bots.START_BANKROLL

    def test_settlement_yes_and_no(self):
        bots.open_position("KELLY", "SF1", "KX-Y", "won", 0.60, 60.0)
        bots.open_position("KELLY", "SF1", "KX-N", "lost", 0.60, 60.0)
        with SessionLocal() as s:
            s.add(MarketClosing(match_id="SF1", market_id="KX-Y",
                                event_ticker="E", data_json=json.dumps(
                                    {"result": "yes"})))
            s.add(MarketClosing(match_id="SF1", market_id="KX-N",
                                event_ticker="E", data_json=json.dumps(
                                    {"result": "no"})))
            s.commit()
        assert bots.settle_match("SF1") == 2
        with SessionLocal() as s:
            y = s.query(BotPosition).filter_by(market_id="KX-Y").one()
            n = s.query(BotPosition).filter_by(market_id="KX-N").one()
            assert y.pnl == y.contracts * 1.0 and y.close_reason == "settled yes"
            assert n.pnl == 0.0 and n.close_reason == "settled no"

    def test_unsettled_snapshot_uses_price_heuristic_or_waits(self):
        bots.open_position("FADE", "SF2", "KX-H", "t", 0.50, 60.0)
        bots.open_position("FADE", "SF2", "KX-W", "t", 0.50, 60.0)
        with SessionLocal() as s:
            s.add(MarketClosing(match_id="SF2", market_id="KX-H",
                                event_ticker="E", data_json=json.dumps(
                                    {"result": "", "last_price": "0.9900"})))
            s.add(MarketClosing(match_id="SF2", market_id="KX-W",
                                event_ticker="E", data_json=json.dumps(
                                    {"result": "", "last_price": "0.5000"})))
            s.commit()
        assert bots.settle_match("SF2") == 1            # heuristic yes
        with SessionLocal() as s:
            w = s.query(BotPosition).filter_by(market_id="KX-W").one()
            assert w.closed_at is None                  # waits for backfill

    def test_wire_exit_selection(self):
        bots.open_position("WIRE", "FINAL", "KX-A", "t", 0.50, 40.0)
        bots.open_position("WIRE", "FINAL", "KX-B", "t", 0.50, 40.0)
        with SessionLocal() as s:
            open_pos = s.query(BotPosition).all()
        live = [_live(0.9, 0.72, market_id="KX-A"),   # +22c -> take profit
                _live(0.3, 0.45, market_id="KX-B")]   # SELL signal fired
        exits = bots.wire_exits(open_pos, live, sell_signal_ids={"KX-B"})
        got = {(p.market_id, reason) for p, _, reason in exits}
        assert got == {("KX-A", "take profit +20c"), ("KX-B", "sell signal")}


class TestEndpoint:
    def setup_method(self):
        init_db()
        with SessionLocal() as s:
            s.query(BotPosition).delete()
            s.commit()
        bots.open_position("MOONSHOT", "FINAL", "KX-L", "Exact 3-2", 0.10, 10.0)

    def teardown_method(self):
        with SessionLocal() as s:
            s.query(BotPosition).delete()
            s.commit()

    def test_bots_ledger_shape(self):
        from fastapi.testclient import TestClient
        from api.main import app
        body = TestClient(app).get("/api/bots").json()
        assert body["start_bankroll"] == bots.START_BANKROLL
        assert {b["bot"] for b in body["bots"]} == set(bots.PERSONAS)
        moon = next(b for b in body["bots"] if b["bot"] == "MOONSHOT")
        assert len(moon["open"]) == 1
        assert moon["bankroll"] < bots.START_BANKROLL
        assert moon["equity"] == bots.START_BANKROLL   # cost still in play


class TestSweetspot:
    def _score(self, h, a, model_p, implied):
        return {"market_id": f"KX-{h}{a}", "market_title": f"Exact {h}-{a}",
                "outcome_key": f"score_{h}_{a}",
                "model_probability": model_p, "implied_probability": implied}

    def test_cluster_is_mode_plus_neighbours(self):
        rows = [self._score(1, 1, 0.14, 0.16), self._score(1, 0, 0.10, 0.08),
                self._score(2, 1, 0.09, 0.11), self._score(0, 1, 0.09, 0.07),
                self._score(3, 3, 0.02, 0.05),      # far from the mode: out
                {"market_id": "KX-W", "market_title": "Spain to win",
                 "outcome_key": "home_win",
                 "model_probability": 0.5, "implied_probability": 0.5}]
        entries = bots.sweetspot_entries(rows, 1000)
        got = {e[0] for e in entries}
        assert got == {"KX-11", "KX-10", "KX-21", "KX-01"}

    def test_dutch_split_follows_model_p(self):
        rows = [self._score(1, 1, 0.20, 0.15), self._score(1, 0, 0.15, 0.12)]
        entries = bots.sweetspot_entries(rows, 1000)
        stakes = {e[0]: e[3] for e in entries}
        assert abs(sum(stakes.values()) - 60.0) < 0.01
        assert stakes["KX-11"] > stakes["KX-10"]

    def test_cluster_caps_at_four(self):
        rows = [self._score(i, 0, 0.10, 0.10) for i in range(6)]
        assert len(bots.sweetspot_entries(rows, 1000)) == 4

    def test_no_exact_books_no_entries(self):
        rows = [{"market_id": "KX-W", "market_title": "t",
                 "outcome_key": "home_win",
                 "model_probability": 0.6, "implied_probability": 0.5}]
        assert bots.sweetspot_entries(rows, 1000) == []
