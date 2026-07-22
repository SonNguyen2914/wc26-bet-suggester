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


class TestExpensiveRouteRateLimit:
    def test_second_call_is_limited(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", False)
        monkeypatch.setattr(config, "RATE_LIMIT_SECONDS", 999.0)
        first = client.post("/api/refresh-all")
        assert first.status_code == 200
        second = client.post("/api/refresh-all")
        assert second.status_code == 429
