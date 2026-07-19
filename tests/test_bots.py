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


class TestCrew:
    """v3 contract: two modes + permanent knockout draw insurance +
    model-read 0-0 + belief-weighted stakes."""

    def _score(self, key, implied, model_p=0.05):
        return {"market_id": f"KX-{key}", "market_title": key,
                "outcome_key": key,
                "model_probability": model_p, "implied_probability": implied}

    def _board(self, hw=0.36, aw=0.34, draw_p=0.20, zz_p=0.04, missing=()):
        rows = [
            {"market_id": "KX-HW", "market_title": "home win",
             "outcome_key": "home_win", "model_probability": hw,
             "implied_probability": hw},
            {"market_id": "KX-AW", "market_title": "away win",
             "outcome_key": "away_win", "model_probability": aw,
             "implied_probability": aw},
            {"market_id": "KX-D", "market_title": "Draw",
             "outcome_key": "draw", "model_probability": draw_p,
             "implied_probability": 0.25},
            self._score("score_0_0", 0.07, model_p=zz_p),
        ]
        keys = set(bots.CREW_EVEN_LADDER + ["score_1_1", "score_2_2"] +
                   bots.CREW_STRONG_HOME + bots.CREW_STRONG_AWAY)
        for k in keys:
            if k not in missing:
                rows.append(self._score(k, 0.06))
        return rows

    def test_knockout_even_always_carries_one_one(self):
        # draw read UNDER the trigger — knockout still buys 1-1
        entries = bots.crew_entries(self._board(draw_p=0.20), 1000,
                                    stage="knockout")
        keys = {e[0] for e in entries}
        assert "KX-score_1_1" in keys
        assert "KX-score_2_2" not in keys       # 2-2 stays feel-based

    def test_group_even_insurance_stays_feel_based(self):
        low = bots.crew_entries(self._board(draw_p=0.20), 1000, stage="group")
        hot = bots.crew_entries(self._board(draw_p=0.28), 1000, stage="group")
        assert "KX-score_1_1" not in {e[0] for e in low}
        assert {"KX-score_1_1", "KX-score_2_2"} <= {e[0] for e in hot}

    def test_zero_zero_when_model_reads_cagey(self):
        quiet = bots.crew_entries(self._board(zz_p=0.08), 1000,
                                  stage="knockout")
        open_ = bots.crew_entries(self._board(zz_p=0.03), 1000,
                                  stage="knockout")
        assert "KX-score_0_0" in {e[0] for e in quiet}
        assert "KX-score_0_0" not in {e[0] for e in open_}

    def test_uneven_knockout_keeps_parked_bus_hedge(self):
        entries = bots.crew_entries(self._board(hw=0.55, aw=0.20), 1000,
                                    stage="knockout")
        keys = {e[0] for e in entries}
        assert {f"KX-{k}" for k in bots.CREW_STRONG_HOME} <= keys
        assert "KX-score_1_1" in keys           # the hedge
        assert "KX-score_1_0" not in keys       # ones still dropped

    def test_uneven_group_drops_draws_entirely(self):
        entries = bots.crew_entries(self._board(hw=0.20, aw=0.55), 1000,
                                    stage="group")
        assert {e[0] for e in entries} == \
            {f"KX-{k}" for k in bots.CREW_STRONG_AWAY}

    def test_stakes_follow_belief_and_sum_to_budget(self):
        board = self._board(draw_p=0.28)
        # make 2-1 the crew's conviction score
        for r in board:
            if r["outcome_key"] == "score_2_1":
                r["model_probability"] = 0.15
        entries = bots.crew_entries(board, 1000, stage="knockout")
        stakes = {e[0]: e[3] for e in entries}
        assert abs(sum(stakes.values()) - 60.0) < 0.01
        assert stakes["KX-score_2_1"] == max(stakes.values())

    def test_unpriced_rungs_skipped_budget_redistributed(self):
        entries = bots.crew_entries(
            self._board(missing=("score_2_0", "score_0_2")), 1000,
            stage="group")
        assert len(entries) == 4
        assert abs(sum(e[3] for e in entries) - 60.0) < 0.01


class TestNewBotEntryRules:
    def test_coin_is_seeded_deterministic_and_bounded(self):
        rows = [_row(0.5, 0.02 + i * 0.06, market_id=f"M{i}") for i in range(10)]
        a = bots.coin_entries(rows, 1000, "THIRD")
        assert a == bots.coin_entries(rows, 1000, "THIRD")   # stable per match
        assert len(a) == bots.COIN_PICKS
        assert all(s == bots.COIN_STAKE for _, _, _, s, _ in a)
        assert all(bots.COIN_BAND[0] <= p <= bots.COIN_BAND[1]
                   for _, _, p, _, _ in a)

    def test_sheep_follows_risers_and_ignores_the_model(self):
        rows = [_row(0.01, 0.50, market_id="UP"),     # model hates it; rising
                _row(0.99, 0.50, market_id="DOWN"),   # model loves it; falling
                _row(0.50, 0.50, market_id="FLAT")]
        trends = {"UP": 0.06, "DOWN": -0.06, "FLAT": 0.01}
        entries = bots.sheep_entries(rows, 1000, trends)
        assert [e[0] for e in entries] == ["UP"]
        assert entries[0][3] == bots.SHEEP_STAKE

    def test_sheep_caps_at_the_strongest_risers(self):
        rows = [_row(0.5, 0.5, market_id=f"R{i}") for i in range(5)]
        trends = {f"R{i}": 0.04 + i * 0.01 for i in range(5)}
        entries = bots.sheep_entries(rows, 1000, trends)
        assert len(entries) == bots.SHEEP_MAX
        assert entries[0][0] == "R4"                  # biggest riser first

    def test_sniper_is_kelly_with_a_window_tag(self):
        rows = [_row(0.62, 0.54)]
        k = bots.kelly_entries(rows, 1000)
        s = bots.sniper_entries(rows, 1000)
        assert [(e[0], e[2], e[3]) for e in k] == [(e[0], e[2], e[3]) for e in s]
        assert s[0][4].startswith("T-10 strike")

    @staticmethod
    def _fav(key, implied, market_id):
        r = _row(0.5, implied, market_id=market_id)
        r["outcome_key"] = key
        return r

    def test_tilt_backs_the_favourite_and_doubles(self):
        rows = [self._fav("home_win", 0.55, "H"),
                self._fav("away_win", 0.30, "A"),
                self._fav("draw", 0.60, "D")]         # draws never count
        (mk, _, _, stake, _), = bots.tilt_entries(rows, 1000, 0)
        assert mk == "H" and stake == bots.TILT_BASE
        (_, _, _, stake3, _), = bots.tilt_entries(rows, 1000, 3)
        assert stake3 == bots.TILT_BASE * 8
        (_, _, _, capped, _), = bots.tilt_entries(rows, 1000, 9)
        assert capped == bots.TILT_CAP

    def test_tilt_skips_when_no_favourite_pays(self):
        assert not bots.tilt_entries([self._fav("home_win", 0.85, "H")], 1000, 0)

    def test_scholar_copies_consensus_not_thin_support(self):
        rows = [_row(0.5, 0.40, market_id="POP-X"),
                _row(0.5, 0.40, market_id="THIN-X")]
        support = {"POP-X": 3.0, "THIN-X": 2.0}
        entries = bots.scholar_entries(rows, 1000, support, {}, set())
        assert [e[0] for e in entries] == ["POP-X"]
        assert "consensus" in entries[0][4]

    def test_scholar_refuses_the_banned_family(self):
        rows = [_row(0.5, 0.40, market_id="KXWCTOTAL-X")]
        assert not bots.scholar_entries(rows, 1000, {"KXWCTOTAL-X": 5.0}, {},
                                        {"KXWCTOTAL"})

    def test_scholar_dutches_budget_by_support(self):
        rows = [_row(0.5, 0.40, market_id="A-X"),
                _row(0.5, 0.40, market_id="B-X")]
        support = {"A-X": 6.0, "B-X": 3.0}
        entries = bots.scholar_entries(rows, 1000, support, {"A-X": True},
                                       set())
        stakes = {e[0]: e[3] for e in entries}
        assert round(stakes["A-X"], 2) == 40.0
        assert round(stakes["B-X"], 2) == 20.0
        notes = {e[0]: e[4] for e in entries}
        assert "mentor-led" in notes["A-X"] and "consensus" in notes["B-X"]


class TestLearnerHelpers:
    def setup_method(self):
        init_db()
        with SessionLocal() as s:
            s.query(BotPosition).delete()
            s.commit()

    def test_tilt_streak_counts_trailing_losses_only(self):
        from datetime import timedelta as _td
        t0 = utcnow()
        with SessionLocal() as s:
            s.add_all([
                BotPosition(bot="TILT", match_id="TS", market_id="T1",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=5.0, pnl=10.0, closed_at=t0 - _td(hours=3),
                            close_reason="settled yes"),
                BotPosition(bot="TILT", match_id="TS", market_id="T2",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=5.0, pnl=0.0, closed_at=t0 - _td(hours=2),
                            close_reason="settled no"),
                BotPosition(bot="TILT", match_id="TS", market_id="T3",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=5.0, pnl=0.0, closed_at=t0 - _td(hours=1),
                            close_reason="settled no"),
            ])
            s.commit()
            assert bots._tilt_streak(s) == 2          # win resets the count

    def test_scholar_context_weights_winners_and_bans_loser_families(self):
        with SessionLocal() as s:
            s.add_all([
                # PEERWIN settled +100 -> weight 1 + 100/50 = 3.0
                BotPosition(bot="PEERWIN", match_id="OLD", market_id="W-1",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=50.0, pnl=150.0, closed_at=utcnow(),
                            close_reason="settled yes"),
                # the room dropped $20 on family LOSSFAM -> banned
                BotPosition(bot="PEERLOSE", match_id="OLD",
                            market_id="LOSSFAM-1", market_title="t",
                            entry_price=0.5, contracts=10, cost=20.0, pnl=0.0,
                            closed_at=utcnow(), close_reason="settled no"),
                # open positions on the target match
                BotPosition(bot="PEERWIN", match_id="TGT", market_id="X-1",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=5.0),
                BotPosition(bot="PEERCOLD", match_id="TGT", market_id="X-1",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=5.0),
                BotPosition(bot="SCHOLAR", match_id="TGT", market_id="X-1",
                            market_title="t", entry_price=0.5, contracts=10,
                            cost=5.0),                # self never counts
            ])
            s.commit()
            support, mentor_led, banned = bots._scholar_context("TGT", s)
        assert round(support["X-1"], 2) == 4.0        # 3.0 mentor + 1.0 cold
        assert mentor_led.get("X-1") is True
        assert "LOSSFAM" in banned

    def test_price_trends_tolerates_sqlite_naive_datetimes(self):
        from datetime import timedelta as _td
        from src.db import OddsReading
        with SessionLocal() as s:
            s.query(OddsReading).delete()
            s.add(OddsReading(match_id="PT", market_id="P-1", yes_price=0.40,
                              decimal_odds=2.5,
                              created_at=utcnow() - _td(hours=7)))
            s.commit()
            trends = bots._price_trends(
                "PT", [{"market_id": "P-1", "implied_probability": 0.46}], s)
        assert trends == {"P-1": 0.06}


class TestSettleAndRestore:
    def setup_method(self):
        init_db()
        with SessionLocal() as s:
            s.query(BotPosition).delete()
            s.query(MarketClosing).delete()
            s.commit()

    def test_settle_reads_last_price_dollars(self):
        with SessionLocal() as s:
            s.add(BotPosition(bot="KELLY", match_id="SM", market_id="SM-1",
                              market_title="t", entry_price=0.4, contracts=10,
                              cost=4.2))
            # fresh Kalshi rows carry last_price_dollars, result unset
            s.add(MarketClosing(match_id="SM", market_id="SM-1",
                                event_ticker="E",
                                data_json=json.dumps(
                                    {"result": "",
                                     "last_price_dollars": 0.99})))
            s.commit()
        assert bots.settle_match("SM") == 1
        with SessionLocal() as s:
            pos = s.query(BotPosition).filter_by(market_id="SM-1").one()
            assert pos.close_reason == "settled yes"
            assert pos.pnl == 10.0

    def test_restore_round_trips_the_ledger_export(self):
        from fastapi.testclient import TestClient
        from api.main import app
        client = TestClient(app)
        payload = {"bots": [
            {"bot": "KELLY", "open": [
                {"match_id": "RT", "market_id": "RT-OPEN",
                 "market_title": "open one", "entry_price": 0.38,
                 "contracts": 98, "cost": 38.86, "note": "edge",
                 "opened_at": "2026-07-16T16:00:19"}],
             "closed": [
                {"match_id": "RT", "market_id": "RT-DONE",
                 "market_title": "done one", "entry_price": 0.26,
                 "contracts": 134, "cost": 36.64, "note": "",
                 "opened_at": "2026-07-16T15:56:19",
                 "closed_at": "2026-07-18T23:05:00",
                 "close_price": 1.0, "close_reason": "settled yes",
                 "net": 97.36}]}]}
        r = client.post("/api/bots/restore", json=payload)
        assert r.status_code == 200
        assert r.json() == {"inserted": 2, "skipped": 0}
        # replay is a no-op
        r2 = client.post("/api/bots/restore", json=payload)
        assert r2.json() == {"inserted": 0, "skipped": 2}
        with SessionLocal() as s:
            done = s.query(BotPosition).filter_by(market_id="RT-DONE").one()
            assert done.pnl == 134.0            # net 97.36 + cost 36.64
            assert done.close_reason == "settled yes"
            open_ = s.query(BotPosition).filter_by(market_id="RT-OPEN").one()
            assert open_.closed_at is None
            assert bots.bankroll("KELLY", s) == round(
                1000.0 - 38.86 + 134.0, 2)
