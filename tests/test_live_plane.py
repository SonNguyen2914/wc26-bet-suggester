"""Live-plane schema invariants + dormancy (MLS launch decision O1-O3)."""
from __future__ import annotations

import subprocess
import sys
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.live.models import (Competition, Fixture, LiveBase,
                             PredictionContract, PredictionRun, Team,
                             TeamAlias)


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", future=True)
    LiveBase.metadata.create_all(eng)
    s = sessionmaker(bind=eng, future=True)()
    s.add(Competition(slug="mls-2026", name="MLS", season=2026))
    s.add(Team(id=1, competition_slug="mls-2026",
               canonical_name="Columbus Crew"))
    s.add(Fixture(id=10, competition_slug="mls-2026",
                  espn_event_id="761668"))
    s.commit()
    return s


class TestCanonicalT10Invariant:
    def test_one_canonical_complete_t10_per_fixture(self, session):
        session.add(PredictionRun(id="run-1", fixture_id=10,
                                  run_type="t10", status="complete",
                                  canonical=True))
        session.commit()
        session.add(PredictionRun(id="run-2", fixture_id=10,
                                  run_type="t10", status="complete",
                                  canonical=True))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_non_canonical_and_other_types_are_free(self, session):
        for i, kw in enumerate((
                dict(run_type="t10", status="complete", canonical=False),
                dict(run_type="t10", status="failed", canonical=True),
                dict(run_type="t60", status="complete", canonical=True),
                dict(run_type="live", status="complete", canonical=True))):
            session.add(PredictionRun(id=f"free-{i}", fixture_id=10, **kw))
        session.commit()    # no violation: the index is precisely scoped

    def test_run_contract_uniqueness(self, session):
        session.add(PredictionRun(id="run-3", fixture_id=10,
                                  run_type="scheduled", status="complete"))
        session.commit()
        session.add(PredictionContract(prediction_run_id="run-3",
                                       market_contract_id=None,
                                       outcome_key="home_win",
                                       raw_probability=0.5))
        session.commit()
        # NULL market_contract_id rows don't collide (SQL NULL semantics);
        # real contract ids do:
        from src.live.models import MarketContract, MarketEvent
        session.add(MarketEvent(id=1, kalshi_event_ticker="KX-TEST",
                                competition_slug="mls-2026"))
        session.add(MarketContract(id=5, market_event_id=1, ticker="KX-T-A"))
        session.commit()
        session.add(PredictionContract(prediction_run_id="run-3",
                                       market_contract_id=5,
                                       outcome_key="draw",
                                       raw_probability=0.3))
        session.commit()
        session.add(PredictionContract(prediction_run_id="run-3",
                                       market_contract_id=5,
                                       outcome_key="draw",
                                       raw_probability=0.31))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


class TestIdentityRules:
    def test_alias_unique_per_source(self, session):
        session.add(TeamAlias(team_id=1, alias="Columbus", source="kalshi",
                              approved=True))
        session.commit()
        session.add(TeamAlias(team_id=1, alias="Columbus", source="kalshi"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        # same alias under another source is fine
        session.add(TeamAlias(team_id=1, alias="Columbus", source="espn"))
        session.commit()

    def test_alias_defaults_unapproved(self, session):
        a = TeamAlias(team_id=1, alias="CLB Crew", source="kalshi")
        session.add(a)
        session.commit()
        assert a.approved is False      # fuzzy may propose, never decide


class TestDormancy:
    def test_plane_dormant_without_url(self):
        from src.live import db as livedb
        assert livedb.live_enabled() is False
        assert livedb.get_engine() is None
        assert livedb.get_session() is None
        assert livedb.migrations_current() is None
        livedb.startup_check()          # dormant -> no raise


class TestMigrations:
    def test_baseline_upgrades_empty_database(self, tmp_path):
        # repo root derived from THIS file — a hardcoded author-machine
        # path made the suite non-portable (V8 evaluation audit)
        import os
        repo_root = os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))
        url = f"sqlite:///{tmp_path}/mig.db"
        r = subprocess.run(
            [sys.executable, "-m", "alembic",
             "-x", f"url={url}", "upgrade", "head"],
            capture_output=True, text=True, cwd=repo_root)
        assert r.returncode == 0, r.stderr
        import sqlite3
        tables = {row[0] for row in sqlite3.connect(
            f"{tmp_path}/mig.db").execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"prediction_run", "market_quote", "team_alias",
                "alembic_version"} <= tables


class TestUrlNormalization:
    def test_provider_schemes_route_to_psycopg3(self):
        from config import _normalize_pg_url
        assert _normalize_pg_url(
            "postgres://u:p@h:5432/db"
        ) == "postgresql+psycopg://u:p@h:5432/db"
        assert _normalize_pg_url(
            "postgresql://u:p@h/db"
        ) == "postgresql+psycopg://u:p@h/db"
        # already-pinned and sqlite URLs pass through untouched
        assert _normalize_pg_url(
            "postgresql+psycopg://u@h/db"
        ) == "postgresql+psycopg://u@h/db"
        assert _normalize_pg_url("sqlite:///x.db") == "sqlite:///x.db"
        assert _normalize_pg_url("") == ""


class TestPlaneIsolation:
    def test_live_boot_failure_never_raises(self, monkeypatch):
        """A live-plane failure must not be able to kill the archive."""
        import config
        from src.live import db as livedb
        monkeypatch.setattr(config, "LIVE_DATABASE_URL",
                            "postgresql+psycopg://bad:bad@127.0.0.1:1/x")
        monkeypatch.setattr(livedb, "_engine", None)
        monkeypatch.setattr(livedb, "_Session", None)
        monkeypatch.setattr(livedb, "LIVE_BOOT_ERROR", None)
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: type(
            "R", (), {"returncode": 1, "stderr": "boom", "stdout": ""})())
        livedb.migrate_and_seed()          # must NOT raise
        assert livedb.LIVE_BOOT_ERROR and "boom" in livedb.LIVE_BOOT_ERROR
        st = livedb.status()
        assert st["boot_failed"] is True and "boom" in st["error"]


class TestPartialIndexDdl:
    def test_compiles_validly_for_both_dialects(self):
        """`canonical IS 1` killed the first PG migration — pin the DDL."""
        from sqlalchemy.dialects import postgresql, sqlite
        from sqlalchemy.schema import CreateIndex
        idx = next(i for i in
                   __import__("src.live.models", fromlist=["PredictionRun"])
                   .PredictionRun.__table__.indexes
                   if i.name == "uq_fixture_canonical_t10")
        pg = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
        sq = str(CreateIndex(idx).compile(dialect=sqlite.dialect()))
        assert "IS 1" not in pg and "IS 1" not in sq
        assert "canonical AND" in pg          # bare boolean, valid PG
        assert "canonical = 1" in sq          # integer form, valid SQLite
        assert "WHERE" in pg and "UNIQUE" in pg
