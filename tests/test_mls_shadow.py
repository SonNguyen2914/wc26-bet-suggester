"""MLS shadow pipeline: identity, ingestion, model math, prediction
runs (launch decision O4-O8). All canned — no network anywhere."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import config
from src.live import db as live_db
from src.live import identity, ingest, markets, model_mls, runs
from src.live.models import (Competition, Fixture, FixtureChange, LiveBase,
                             PredictionRun, Team, TeamAlias)

UTC = timezone.utc


@pytest.fixture()
def live_session(tmp_path, monkeypatch):
    """Point the whole live plane at a throwaway sqlite file so module
    code paths (identity/ingest/runs) run exactly as in production."""
    url = f"sqlite:///{tmp_path}/live.db"
    monkeypatch.setattr(config, "LIVE_DATABASE_URL", url)
    monkeypatch.setattr(live_db, "_engine", None)
    monkeypatch.setattr(live_db, "_Session", None)
    monkeypatch.setattr(live_db, "LIVE_BOOT_ERROR", None)
    LiveBase.metadata.create_all(live_db.get_engine())
    s = live_db.get_session()
    s.add(Competition(slug="mls-2026", name="MLS", season=2026))
    s.commit()
    yield s
    s.close()
    monkeypatch.setattr(live_db, "_engine", None)
    monkeypatch.setattr(live_db, "_Session", None)


CANNED_ESPN = [
    {"id": 183, "displayName": "Columbus Crew",
     "shortDisplayName": "Columbus", "abbreviation": "CLB"},
    {"id": 17606, "displayName": "New York City FC",
     "shortDisplayName": "New York City", "abbreviation": "NYC"},
    {"id": 21812, "displayName": "St. Louis CITY SC",
     "shortDisplayName": "St. Louis", "abbreviation": "STL"},
    {"id": 9720, "displayName": "CF Montréal",
     "shortDisplayName": "Montréal", "abbreviation": "MTL"},
]


class TestIdentity:
    def test_seed_is_idempotent_and_bridges_are_approved(self, live_session):
        r1 = identity.seed_teams(CANNED_ESPN)
        r2 = identity.seed_teams(CANNED_ESPN)
        assert r1["teams"] == 4 and r2["added_teams"] == 0
        # curated bridge -> approved kalshi alias -> resolves
        t = identity.resolve("kalshi", "Saint Louis")
        assert t is not None and t.canonical_name == "St. Louis CITY SC"
        # accent-insensitive bridge landed (Montréal)
        assert identity.resolve("kalshi", "Montreal").espn_id == "9720"

    def test_unapproved_alias_never_resolves(self, live_session):
        identity.seed_teams(CANNED_ESPN)
        team = live_session.query(Team).filter_by(
            canonical_name="Columbus Crew").one()
        live_session.add(TeamAlias(team_id=team.id, alias="Cbus",
                                   source="kalshi", approved=False))
        live_session.commit()
        assert identity.resolve("kalshi", "Cbus") is None

    def test_espn_display_names_resolve(self, live_session):
        identity.seed_teams(CANNED_ESPN)
        assert identity.resolve_espn_name("Columbus Crew") is not None
        assert identity.unmapped_upcoming(
            ["Columbus Crew", "Real Madrid"]) == ["Real Madrid"]


def _ev(eid, iso, state, hs=None, as_=None, score_as_dict=False):
    def score(v):
        if v is None:
            return None
        return {"value": v} if score_as_dict else str(v)
    return {
        "id": eid, "date": iso,
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": score(hs),
                 "team": {"displayName": "Columbus Crew"}},
                {"homeAway": "away", "score": score(as_),
                 "team": {"displayName": "New York City FC"}},
            ],
            "venue": {"fullName": "Lower.com Field"},
        }],
        "status": {"type": {"state": state}},
    }


class TestIngest:
    def test_event_parsing_both_shapes(self):
        f = ingest._event_to_fields(
            _ev("1", "2026-07-25T23:30Z", "post", 2, 1))
        assert (f["home_goals"], f["away_goals"]) == (2, 1)
        f2 = ingest._event_to_fields(
            _ev("2", "2026-07-25T23:30Z", "post", 3, 0, score_as_dict=True))
        assert (f2["home_goals"], f2["away_goals"]) == (3, 0)
        # pre-match: never scores, even if the field carries zeros
        f3 = ingest._event_to_fields(
            _ev("3", "2026-08-01T23:30Z", "pre", 0, 0))
        assert f3["home_goals"] is None and f3["status"] == "pre"

    def test_reschedule_creates_history(self, live_session):
        identity.seed_teams(CANNED_ESPN)
        now = datetime.now(UTC)
        f = ingest._event_to_fields(_ev("55", "2026-08-01T23:30Z", "pre"))
        created, _ = ingest._upsert_fixture(live_session, f, now)
        live_session.commit()
        assert created
        row = live_session.query(Fixture).filter_by(
            espn_event_id="55").one()
        assert row.home_team_id is not None       # resolved via alias
        # kickoff moves 2h -> history row + updated current, original kept
        f2 = ingest._event_to_fields(_ev("55", "2026-08-02T01:30Z", "pre"))
        ingest._upsert_fixture(live_session, f2, now)
        live_session.commit()
        changes = live_session.query(FixtureChange).filter_by(
            fixture_id=row.id, field="kickoff").all()
        assert len(changes) == 1
        assert row.original_kickoff_utc != row.current_kickoff_utc

    def test_scores_fill_once_on_completion(self, live_session):
        identity.seed_teams(CANNED_ESPN)
        now = datetime.now(UTC)
        ingest._upsert_fixture(
            live_session,
            ingest._event_to_fields(_ev("77", "2026-07-20T23:30Z", "pre")),
            now)
        live_session.commit()
        ingest._upsert_fixture(
            live_session,
            ingest._event_to_fields(
                _ev("77", "2026-07-20T23:30Z", "post", 2, 1)), now)
        live_session.commit()
        row = live_session.query(Fixture).filter_by(
            espn_event_id="77").one()
        assert (row.status, row.home_goals, row.away_goals) == ("post", 2, 1)
        # a later contradictory payload must NOT rewrite a frozen score
        ingest._upsert_fixture(
            live_session,
            ingest._event_to_fields(
                _ev("77", "2026-07-20T23:30Z", "post", 9, 9)), now)
        live_session.commit()
        assert row.home_goals == 2


def _fx(i, days_ago, home, away, hg, ag):
    ko = datetime.now(UTC) - timedelta(days=days_ago)
    return SimpleNamespace(id=i, espn_event_id=str(i),
                           current_kickoff_utc=ko, status="post",
                           home_team_id=home, away_team_id=away,
                           home_goals=hg, away_goals=ag)


class TestModelFit:
    def test_league_and_venue_params_are_fitted(self):
        # uniform 2-1 home wins: gpg=1.5, venue split 2/1.5 and 1/1.5
        fixtures = [_fx(i, 10 + i, 1 + (i % 2), 3 + (i % 2), 2, 1)
                    for i in range(8)]
        m = model_mls.fit(fixtures, datetime.now(UTC))
        assert m["league_gpg"] == pytest.approx(1.5)
        assert m["venue_home"] == pytest.approx(2 / 1.5)
        assert m["venue_away"] == pytest.approx(1 / 1.5)

    def test_ratings_shrink_and_order(self):
        # team 1 scores 3 every game, team 2 concedes them; 6 games each
        fixtures = [_fx(i, 5 + 7 * i, 1, 2, 3, 0) for i in range(6)]
        m = model_mls.fit(fixtures, datetime.now(UTC))
        assert m["ratings"][1]["attack"] > 1 > m["ratings"][2]["attack"]
        assert m["ratings"][2]["defence"] > 1 > m["ratings"][1]["defence"]
        # shrinkage keeps a 6-game sample well inside the raw rate
        raw = 3 / m["league_gpg"]
        assert m["ratings"][1]["attack"] < raw

    def test_seed_is_deterministic_and_scoped(self):
        assert model_mls.seed_for(10, "t10") == model_mls.seed_for(10, "t10")
        assert model_mls.seed_for(10, "t10") != model_mls.seed_for(10, "scheduled")
        assert model_mls.seed_for(10, "t10") != model_mls.seed_for(11, "t10")

    def test_seed_fits_signed_32bit(self):
        # prediction_run.simulation_seed is INTEGER on PostgreSQL —
        # an unmasked seed >= 2^31 killed the prod boot sweep (Jul 23)
        for fid in range(1, 600):
            for rt in ("scheduled", "t10", "backtest"):
                assert 0 <= model_mls.seed_for(fid, rt) < 2**31

    def test_predict_requires_min_games_and_is_deterministic(self):
        fixtures = [_fx(i, 5 + i, 1, 2, 2, 1) for i in range(6)]
        m = model_mls.fit(fixtures, datetime.now(UTC))
        target = _fx(99, -1, 1, 2, None, None)
        p1 = model_mls.predict_fixture(target, m, n_sims=500)
        p2 = model_mls.predict_fixture(target, m, n_sims=500)
        assert p1["outcomes"] == p2["outcomes"]          # seeded
        assert sum(p1["outcomes"].values()) == pytest.approx(1.0, abs=0.01)
        # unknown team -> no prediction, never a default-stats guess
        assert model_mls.predict_fixture(
            _fx(98, -1, 1, 42, None, None), m) is None


class TestMarketHelpers:
    def test_cents_prefers_native_integer(self):
        assert markets._cents({"yes_bid": 57,
                               "yes_bid_dollars": "0.58"}, "yes_bid") == 57
        assert markets._cents({"yes_bid_dollars": "0.58"}, "yes_bid") == 58
        assert markets._cents({}, "yes_bid") is None

    def test_ticker_date_and_et_date_agree(self):
        assert markets._ticker_date(
            "KXMLSGAME-26JUL26CLBNYC") == "26JUL26"
        # 01:30 UTC Jul 27 is still Jul 26 in ET
        dt = datetime(2026, 7, 27, 1, 30, tzinfo=UTC)
        assert markets._fixture_et_date(dt) == "26JUL26"


class TestPredictionRuns:
    def _seed_playable(self, s, n_completed=12):
        identity.seed_teams(CANNED_ESPN)
        teams = {t.canonical_name: t.id for t in
                 s.query(Team).filter_by(competition_slug="mls-2026")}
        ids = list(teams.values())
        now = datetime.now(UTC)
        # a small round-robin history so every team clears MIN_GAMES
        k = 0
        for rnd in range(6):
            for a, b in ((0, 1), (2, 3), (0, 2), (1, 3)):
                k += 1
                s.add(Fixture(
                    competition_slug="mls-2026", espn_event_id=f"h{k}",
                    home_team_id=ids[a], away_team_id=ids[b],
                    current_kickoff_utc=now - timedelta(days=3 * rnd + 2),
                    original_kickoff_utc=now - timedelta(days=3 * rnd + 2),
                    status="post", home_goals=(a + 1) % 3,
                    away_goals=b % 2))
        up = Fixture(competition_slug="mls-2026", espn_event_id="9001",
                     home_team_id=ids[0], away_team_id=ids[1],
                     current_kickoff_utc=now + timedelta(hours=20),
                     original_kickoff_utc=now + timedelta(hours=20),
                     status="pre")
        s.add(up)
        s.commit()
        return up

    def test_scheduled_run_end_to_end(self, live_session, monkeypatch):
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        self._seed_playable(live_session)
        r = runs.scheduled_runs()
        assert r["created"] >= 1
        board = runs.latest_odds()
        row = next(o for o in board if o["espn_event_id"] == "9001")
        assert sum(row["outcomes"].values()) == pytest.approx(1.0, abs=0.01)
        assert row["run_type"] == "scheduled" and not row["locked"]
        # freshness: an immediate second sweep creates nothing
        assert runs.scheduled_runs()["created"] == 0
        # the hub payload carries provenance
        hub = runs.model_for_event("9001")
        assert hub["shadow"] is True
        assert hub["latest"]["seed"] == model_mls.seed_for(
            live_session.query(Fixture).filter_by(
                espn_event_id="9001").one().id, "scheduled")

    def test_incomplete_runs_are_invisible(self, live_session):
        up = self._seed_playable(live_session)
        live_session.add(PredictionRun(
            id="w-1", fixture_id=up.id, run_type="scheduled",
            status="writing", captured_at=datetime.now(UTC)))
        live_session.commit()
        assert runs.latest_odds() == []          # writing != complete
        assert runs.model_for_event("9001") is None

    def test_t10_lock_is_canonical_and_single(self, live_session,
                                              monkeypatch):
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        up.current_kickoff_utc = datetime.now(UTC) + timedelta(minutes=9)
        live_session.commit()
        sent = []
        monkeypatch.setattr(markets, "capture_quotes",
                            lambda fixture_id=None, **kw: {"quotes": 0})
        import src.alerts as alerts
        monkeypatch.setattr(alerts, "send_alert",
                            lambda msg, **kw: sent.append(msg))
        assert runs.t10_locks()["locked"] == 1
        assert runs.t10_locks()["locked"] == 0        # already locked
        assert len(sent) == 1 and "PAPER" in sent[0]
        lock = live_session.query(PredictionRun).filter_by(
            run_type="t10", canonical=True).one()
        assert lock.status == "complete"
        assert runs.model_for_event("9001")["t10_lock"] is not None

    def test_shadow_counts_shape(self, live_session):
        self._seed_playable(live_session)
        c = runs.shadow_counts()
        assert c["teams"] == 4 and c["fixtures"] == 25
        assert c["completed_fixtures"] == 24 and c["t10_locks"] == 0
