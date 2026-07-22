"""Canonical-archive serving + side-effect-free public GETs
(V7 evaluation F1, F2, F7)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import config
from src.db import Prediction, SessionLocal, init_db


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    from api import main as api_main
    init_db()
    api_main._rate_last.clear()
    return TestClient(api_main.app)


def _pred_rows(match_id):
    with SessionLocal() as s:
        return s.execute(select(func.count()).select_from(Prediction)
                         .where(Prediction.match_id == match_id)
                         ).scalar_one()


class TestCanonicalLockServing:
    def test_research_serves_bundle_when_db_empty(self, client):
        r = client.get("/api/research/FINAL").json()
        assert r["final_lock_source"] in ("canonical_archive", "database")
        if r["final_lock_source"] == "canonical_archive":
            assert len(r["final_lock"]) == 49
        # either way the FINAL's frozen record must never be empty
        assert len(r["final_lock"]) >= 45
        keys = {row["outcome_key"] for row in r["final_lock"]}
        assert "home_advance" in keys      # the champion call is in there

    def test_review_page_serves_frozen_archive(self, client, monkeypatch):
        from api import main as api_main
        monkeypatch.setattr(api_main.live_state_svc, "is_finished",
                            lambda mid: True)
        r = client.get("/api/prediction/FINAL").json()
        assert r["source"] == "canonical_archive"
        assert r["is_final"] is True
        assert len(r["markets"]) == 49
        assert r["xg"] is None             # not archived -> never invented
        assert r["summary"] is None

    def test_no_retro_simulation_for_lockless_match(self, client,
                                                    monkeypatch):
        # CAN_MAR predates the repo: no lock bundle exists. The review
        # path must say archive_incomplete — and persist NOTHING (the old
        # fallback re-simulated with the current model).
        from api import main as api_main
        monkeypatch.setattr(api_main.live_state_svc, "is_finished",
                            lambda mid: True)
        before = _pred_rows("CAN_MAR")
        r = client.get("/api/prediction/CAN_MAR").json()
        assert r["source"] == "archive_incomplete"
        assert r["markets"] == [] and r["xg"] is None
        assert "archive_note" in r
        assert _pred_rows("CAN_MAR") == before


class TestSideEffectFreeGets:
    def test_anon_get_never_persists_in_read_only(self, client,
                                                  monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        before = _pred_rows("POR_ESP")
        r = client.get("/api/prediction/POR_ESP")
        assert r.status_code == 200
        assert r.json()["freshness"] in ("unavailable", "stale-archive",
                                         "cached", "locked",
                                         "archive-incomplete")
        assert _pred_rows("POR_ESP") == before      # ZERO rows written

    def test_operator_get_may_compute(self, client, monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        before = _pred_rows("POR_ESP")
        r = client.get("/api/prediction/POR_ESP",
                       headers={"X-Admin-Token": "s3cret"})
        assert r.status_code == 200
        assert _pred_rows("POR_ESP") > before       # operator computes

    def test_unauthorized_force_refresh_is_403_not_silent(self, client,
                                                          monkeypatch):
        monkeypatch.setattr(config, "PUBLIC_READ_ONLY", True)
        monkeypatch.setattr(config, "ADMIN_TOKEN", "s3cret")
        r = client.get("/api/prediction/POR_ESP?force_refresh=true")
        assert r.status_code == 403                 # refused, not downgraded


class TestReadiness:
    def test_ready_reports_counts(self, client):
        r = client.get("/api/ready").json()
        for k in ("ready", "results", "expected_results",
                  "ledger_positions", "expected_ledger",
                  "lock_bundles", "expected_lock_bundles"):
            assert k in r
        assert r["lock_bundles"] == 6               # bundles ship in-repo
        assert r["ready"] is False                  # empty test DB
