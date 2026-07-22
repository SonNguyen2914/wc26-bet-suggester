"""Regression tests for the Jul 21 independent-evaluation fixes:

  1. set-piece double counting  -> centered adjustment (xg_model v2)
  2. first-goal mixture math    -> per-draw probabilities (Jensen)
  3. Kelly fee treatment        -> all-in cost in gate AND fraction
  4. public lockdown            -> PUBLIC_READ_ONLY + ADMIN_TOKEN + 429s

Each test encodes the original defect so a regression reproduces the
evaluation's finding, not just a behavior change.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from src import bots
from src.models.simulator import MatchSimulator
from src.models.xg_model import (LEAGUE_BASE_XG, SET_PIECE_BASELINE,
                                 predict_xg)

_TEAM = {"attack": 1.20, "defence": 0.80, "form": 0.70,
         "set_piece_threat": SET_PIECE_BASELINE, "red_card_risk": 0.0,
         "fatigue": 0.0, "elo": 1800}


def _team(**over):
    t = dict(_TEAM)
    t.update(over)
    return t


class TestSetPieceCentering:
    def test_baseline_pinned_to_stats_table(self):
        # The centering constant must track the sourced table; if stats
        # change, this fails and SET_PIECE_BASELINE must be re-derived.
        from src.schedule_data import TEAM_STATS
        vals = [v.get("set_piece_threat", 0.0) for v in TEAM_STATS.values()]
        mean = sum(vals) / len(vals)
        assert abs(mean - SET_PIECE_BASELINE) < 0.005

    def test_average_team_gets_pure_open_play(self):
        # A team AT the competition mean receives no set-piece term at all
        # — the v1 bug was re-adding the baseline on top of total-xG attack.
        from src.models.features import build_team_features
        h = build_team_features(_team())
        a = build_team_features(_team())
        open_h = (LEAGUE_BASE_XG * h["attack"] * a["defence"]
                  * (0.85 + 0.30 * h["form"]) * h["fatigue_mult"])
        xg_h, xg_a = predict_xg(_team(), _team())
        assert xg_h == pytest.approx(round(open_h, 3), abs=1e-9)
        assert xg_h == xg_a

    def test_only_deviation_moves_xg(self):
        base_h, _ = predict_xg(_team(), _team())
        up_h, _ = predict_xg(
            _team(set_piece_threat=SET_PIECE_BASELINE + 0.10), _team())
        down_h, _ = predict_xg(
            _team(set_piece_threat=SET_PIECE_BASELINE - 0.10), _team())
        assert up_h == pytest.approx(base_h + 0.10, abs=1e-3)
        assert down_h == pytest.approx(base_h - 0.10, abs=1e-3)


class TestFirstGoalMixture:
    def test_outcomes_sum_to_one(self):
        sim = MatchSimulator()
        out = sim.simulate(_team(), _team())
        p = out["props"]
        total = (p["home_first_goal"] + p["away_first_goal"] + p["no_goal"])
        assert total == pytest.approx(1.0, abs=2e-3)   # rounding only

    def test_mixture_raises_no_goal_mass(self, monkeypatch):
        # cv=0 collapses to the old mean-rate shortcut (they must agree);
        # gamma variance must RAISE no_goal above it (Jensen:
        # E[exp(-lam)] > exp(-E[lam])) — the v1 shortcut discarded this.
        monkeypatch.setattr(config, "GOAL_DISPERSION_CV", 0.0)
        flat = MatchSimulator().simulate(_team(), _team())["props"]["no_goal"]
        monkeypatch.setattr(config, "GOAL_DISPERSION_CV", 0.6)
        mixed = MatchSimulator().simulate(_team(), _team())["props"]["no_goal"]
        assert mixed > flat * 1.05     # material, not Monte Carlo noise


class TestKellyAllInCost:
    def _row(self, p, c):
        return {"market_id": "M", "market_title": "t",
                "model_probability": p, "implied_probability": c}

    def test_gate_uses_all_in_cost(self):
        # 5.0pt raw edge but only ~3.3pt after the entry fee: the v1 gate
        # (p - c) admitted this trade; the all-in gate must refuse it.
        assert not bots.kelly_entries([self._row(0.60, 0.55)], 1000)

    def test_fraction_uses_all_in_cost(self):
        (_, _, _, stake, _), = bots.kelly_entries([self._row(0.62, 0.54)],
                                                  1000)
        q = 0.54 + bots.fee(0.54)
        expect = 1000 * ((0.62 - q) / (1.0 - q)) / 2.0
        assert stake == pytest.approx(expect, abs=0.01)
        # v1 sized at the raw quote — visibly larger:
        assert stake < 1000 * ((0.62 - 0.54) / (1.0 - 0.54)) / 2.0


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    from src.db import init_db
    from api import main as api_main
    init_db()
    api_main._rate_last.clear()
    return TestClient(api_main.app)


class TestPublicLockdown:
    def test_default_mode_allows_mutations(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", False)
        r = client.post("/api/settings", json={"min_edge": 0.05})
        assert r.status_code == 200

    def test_read_only_blocks_every_mutating_verb(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "")
        for method, path in (("post", "/api/settings"),
                             ("post", "/api/bots/restore"),
                             ("post", "/api/alerts/test"),
                             ("delete", "/api/positions/1")):
            r = getattr(client, method)(path, **(
                {"json": {}} if method == "post" else {}))
            assert r.status_code == 403, (method, path, r.status_code)

    def test_reads_stay_open_in_read_only(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/bots").status_code == 200

    def test_admin_token_reopens_mutations(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        ok = client.post("/api/settings", json={"min_edge": 0.05},
                         headers={"X-Admin-Token": "s3cret"})
        assert ok.status_code == 200
        bad = client.post("/api/settings", json={"min_edge": 0.05},
                          headers={"X-Admin-Token": "wrong"})
        assert bad.status_code == 403

    def test_unset_token_never_matches_empty_header(self, client,
                                                    monkeypatch):
        # ADMIN_TOKEN="" must NOT mean "empty header passes".
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "")
        r = client.post("/api/settings", json={"min_edge": 0.05},
                        headers={"X-Admin-Token": ""})
        assert r.status_code == 403

    def test_bearer_authorization_works(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        ok = client.post("/api/settings", json={"min_edge": 0.05},
                         headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200

    def test_malformed_authorization_fails(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        for header in ("s3cret",              # no scheme
                       "Basic s3cret",        # wrong scheme
                       "Bearer",              # no token
                       "Bearer  "):           # blank token
            r = client.post("/api/settings", json={"min_edge": 0.05},
                            headers={"Authorization": header})
            assert r.status_code == 403, header

    def test_auth_precedes_rate_bucket(self, client, monkeypatch):
        # An unauthenticated caller must not consume the expensive-route
        # limiter and lock the operator out.
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        monkeypatch.setattr(config, "RATE_LIMIT_SECONDS", 999.0)
        for _ in range(3):
            assert client.post("/api/refresh-all").status_code == 403
        ok = client.post("/api/refresh-all",
                         headers={"X-Admin-Token": "s3cret"})
        assert ok.status_code == 200      # limiter untouched by the 403s


class TestBidSideExecution:
    """Jul 21 evaluation: sells realize the BID; an absent bid means the
    exit is NOT EXECUTABLE — the ask is never silently substituted."""

    def test_yes_bid_extraction_priorities(self):
        from src.kalshi_client import _market_yes_bid
        assert _market_yes_bid({"yes_bid_dollars": "0.5500"}) == 0.55
        # derived from the no-side ask when yes bid missing
        assert _market_yes_bid({"no_ask_dollars": "0.4700"}) == \
            pytest.approx(0.53)
        assert _market_yes_bid({"yes_bid": 42}) == 0.42
        assert _market_yes_bid({"yes_ask_dollars": "0.60"}) is None
        assert _market_yes_bid({}) is None

    def test_wire_exits_fill_at_bid(self):
        pos = type("P", (), {"market_id": "M1", "entry_price": 0.40})()
        rows = [{"market_id": "M1", "market_probability": 0.70,
                 "market_yes_bid": 0.63}]
        (got_pos, price, reason), = bots.wire_exits([pos], rows, set())
        assert price == 0.63              # the bid, never the 0.70 ask
        assert reason == "take profit +20c"

    def test_wire_holds_without_a_bid(self):
        pos = type("P", (), {"market_id": "M1", "entry_price": 0.40})()
        rows = [{"market_id": "M1", "market_probability": 0.70,
                 "market_yes_bid": None}]
        assert bots.wire_exits([pos], rows, {"M1"}) == []   # not executable

    def test_tracker_verdict_uses_bid_or_no_bid(self):
        from src.positions import _verdict, fee
        verdict, hold_ev, cashout = _verdict(0.50, 0.80, 100, 50.0)
        assert verdict == "EXIT"
        assert cashout == pytest.approx(100 * (0.80 - fee(0.80)))
        verdict, hold_ev, cashout = _verdict(0.50, None, 100, 50.0)
        assert verdict == "NO_BID" and cashout is None

    def test_demo_rows_carry_a_bid(self):
        from src.kalshi_client import _demo_markets_for_match
        from src.schedule_data import load_schedule
        mkts = _demo_markets_for_match(load_schedule()[0])
        assert all(0 < m["yes_bid"] <= m["yes_price"] for m in mkts)


class TestExpensiveRouteRateLimit:
    def test_second_call_is_limited(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", False)
        monkeypatch.setattr(config, "RATE_LIMIT_SECONDS", 999.0)
        first = client.post("/api/refresh-all")
        assert first.status_code == 200
        second = client.post("/api/refresh-all")
        assert second.status_code == 429


class TestStrictReadOnlyParsing:
    """V7 evaluation F2: unknown boolean values must fail CLOSED."""

    def test_only_exact_off_values_open(self):
        from config import _parse_read_only
        for raw in ("false", "False", "0", "no", "off", " false "):
            assert _parse_read_only(raw) is False, raw

    def test_everything_else_is_read_only(self):
        from config import _parse_read_only
        for raw in (None, "", "true", "TRUE", "yes", "1", "on",
                    "true ", " treu", "treu", "flase", "enabled", "public"):
            assert _parse_read_only(raw) is True, raw


class TestLiveFirstGoalInterval:
    """V7 evaluation F3: live no-goal must track the REMAINING interval."""

    def _live(self, minute, h=0, a=0):
        sim = MatchSimulator()
        return sim.simulate_remaining(
            _team(), _team(), current_home=h, current_away=a,
            minutes_elapsed=minute, stage="group")

    def test_no_goal_rises_with_the_clock(self):
        vals = [self._live(m)["props"]["no_goal"] for m in (0, 45, 80, 89)]
        assert vals == sorted(vals)            # monotonic in minute
        assert vals[-1] > 0.85                 # ~one quiet minute left
        assert vals[0] < 0.25                  # full match still to play

    def test_minute_zero_matches_prematch(self):
        pre = MatchSimulator().simulate(_team(), _team())["props"]["no_goal"]
        live0 = self._live(0)["props"]["no_goal"]
        assert live0 == pytest.approx(pre, abs=0.03)   # same interval, MC noise

    def test_first_goal_props_absent_after_a_goal(self):
        props = self._live(60, h=1, a=0)["props"]
        assert "no_goal" not in props
        assert "home_first_goal" not in props

    def test_outcomes_still_sum_to_one_live(self):
        p = self._live(30)["props"]
        total = p["home_first_goal"] + p["away_first_goal"] + p["no_goal"]
        assert total == pytest.approx(1.0, abs=2e-3)


class TestMultiRedCardPersistence:
    """V7 evaluation F4: two reds for one team must survive every layer."""

    def test_two_reds_persist_and_reload(self):
        from src.db import (MatchLiveSnapshot, MatchResult, SessionLocal,
                            init_db)
        init_db()
        with SessionLocal() as s:
            s.merge(MatchResult(match_id="REDTEST", home="A", away="B",
                                home_goals=1, away_goals=0,
                                red_home=2, red_away=1))
            s.merge(MatchLiveSnapshot(match_id="REDTEST", home_goals=0,
                                      away_goals=0,
                                      red_home=2, red_away=0))
            s.commit()
        with SessionLocal() as s:
            r = s.get(MatchResult, "REDTEST")
            snap = s.get(MatchLiveSnapshot, "REDTEST")
            assert r.red_home == 2 and r.red_away == 1
            assert snap.red_home == 2
            # legacy truthiness callers still behave
            assert bool(r.red_home) and not bool(0)

    def test_counts_flow_into_the_simulator(self):
        sim = MatchSimulator()
        one = sim.simulate_remaining(_team(), _team(), current_home=0,
                                     current_away=0, minutes_elapsed=60,
                                     stage="group", red_home=1)
        two = sim.simulate_remaining(_team(), _team(), current_home=0,
                                     current_away=0, minutes_elapsed=60,
                                     stage="group", red_home=2)
        # a second red must further depress the carded side's win chance
        assert two["outcomes"]["home_win"] < one["outcomes"]["home_win"]


class TestUnifiedFeeEconomics:
    """V7 evaluation F5: the suggester's gate/EV use all-in cost from the
    ONE shared execution module — the evaluator's marginal example."""

    def test_marginal_trade_refused_all_in(self):
        from src import execution
        # gross edge exactly 5.0pt; all-in edge only ~3.3pt
        assert 0.55 - 0.50 == pytest.approx(0.05)
        assert execution.net_edge(0.55, 0.50) == pytest.approx(0.0325)
        assert execution.net_edge(0.55, 0.50) < 0.05

    def test_net_ev_matches_all_in_cost(self):
        from src import execution
        q = 0.50 + execution.fee(0.50)
        expect = 0.55 * (1 - q) / q - 0.45
        assert execution.net_ev(0.55, 0.50) == pytest.approx(expect)

    def test_suggester_persists_net_edge(self, monkeypatch):
        monkeypatch.setattr(config, "DEMO_MODE", True)
        from sqlalchemy import select
        from src.db import Prediction, SessionLocal, init_db
        from src.schedule_data import get_match
        from src.suggester import SuggesterEngine
        from src import execution
        init_db()
        eng = SuggesterEngine()
        eng.run_for_match(get_match("POR_ESP"), source="test_fee")
        with SessionLocal() as s:
            rows = s.execute(select(Prediction).where(
                Prediction.source == "test_fee")).scalars().all()
        assert rows
        for r in rows[:10]:
            expect = execution.net_edge(r.model_probability,
                                        r.implied_probability)
            assert r.edge == pytest.approx(expect, abs=1e-9)

    def test_single_fee_source(self):
        from src import bots, execution, positions
        assert bots.fee is execution.fee
        assert positions.fee is execution.fee
