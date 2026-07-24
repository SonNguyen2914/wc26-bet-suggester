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

    def test_seed_survives_database_rebuild(self):
        """F10 acceptance: the same PROVIDER fixture keeps its seed even
        when the auto-increment row id changes across rebuilds."""
        a = SimpleNamespace(id=1, espn_event_id="761680")
        b = SimpleNamespace(id=2, espn_event_id="761680")
        assert model_mls.seed_for(a, "t10") == model_mls.seed_for(b, "t10")
        c = SimpleNamespace(id=1, espn_event_id="761681")
        assert model_mls.seed_for(a, "t10") != model_mls.seed_for(c, "t10")

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

    def test_props_cover_every_kalshi_family(self):
        fixtures = [_fx(i, 5 + i, 1, 2, 2, 1) for i in range(6)]
        m = model_mls.fit(fixtures, datetime.now(UTC))
        p = model_mls.predict_fixture(_fx(99, -1, 1, 2, None, None),
                                      m, n_sims=800)
        props = p["props"]
        # totals ladder, margins (spread), first goal, team totals
        for k in ("over_0_5", "over_5_5", "home_margin_2",
                  "away_margin_3", "home_first_goal",
                  "home_team_over_0_5", "away_team_over_2_5"):
            assert k in props, k
        # team totals must be internally consistent (monotone ladder)
        assert (props["home_team_over_0_5"]
                >= props["home_team_over_1_5"]
                >= props["home_team_over_2_5"])
        assert len(p["scorelines"]) == 12


class TestTeamTotalMarginals:
    """V8 evaluation F4: team totals must come from the full goal
    arrays. The old scoreline-sum method understated them by ~2pp in a
    typical match and by >28pp at high scoring rates."""

    def _sim(self, attack):
        from src.models.simulator import MatchSimulator
        raw = {"attack": attack, "defence": attack, "form": 0.5,
               "fatigue": 0.0, "set_piece_threat": 0.30,
               "red_card_risk": 0.06, "elo": 1500.0}
        return MatchSimulator(n_simulations=20000, seed=7).simulate(
            raw, dict(raw), stage="group")

    def test_marginals_exceed_truncated_scoreline_sums_at_high_xg(self):
        out = self._sim(attack=1.8)          # the evaluator's stress
        top30 = out["scorelines"]
        mass = sum(s["prob"] for s in top30)
        assert 0.5 < mass < 1.0              # visibly truncated regime
        for key, idx, n in (("home_team_over_0_5", 0, 1),
                            ("away_team_over_2_5", 1, 3)):
            truncated = sum(s["prob"] for s in top30
                            if int(s["score"].split("-")[idx]) >= n)
            # the marginal must carry the mass the display list dropped
            assert out["props"][key] > truncated + 0.02

    def test_ladder_is_coherent(self):
        out = self._sim(attack=1.0)
        p = out["props"]
        assert (p["home_team_over_0_5"] >= p["home_team_over_1_5"]
                >= p["home_team_over_2_5"] > 0)
        # over_0_5 for a team implies at least one goal in the match
        assert p["over_0_5"] >= p["home_team_over_0_5"]


class TestCurrentProviderSchema:
    """V8 evaluation F5: sizes, volume, OI, provider time, rules and
    DEPTH now arrive as *_fp / *_dollars / orderbook_fp — the exact
    current shapes (captured from live responses Jul 23) must parse."""

    CURRENT = {
        "ticker": "KXMLSGAME-26JUL25CLBCIN-CLB",
        "status": "active",
        "yes_bid": 54, "yes_ask": 55,        # integer cents present
        "yes_bid_dollars": "0.5400", "yes_ask_dollars": "0.5500",
        "no_bid_dollars": "0.4500", "no_ask_dollars": "0.4600",
        "last_price_dollars": "0.5400",
        "yes_bid_size_fp": "2642.00", "yes_ask_size_fp": "2902.00",
        "no_bid_size_fp": "100.00", "no_ask_size_fp": "50.00",
        "volume_fp": "135.50", "open_interest_fp": "135.50",
        "updated_time": "2026-07-23T11:00:00Z",
        "rules_primary": "Resolves YES if Columbus wins.",
    }

    def test_quote_row_parses_current_fields(self):
        q = markets._quote_row(self.CURRENT, mc_id=1, obs_id=1)
        assert (q.yes_bid_c, q.yes_ask_c) == (54, 55)
        assert q.yes_bid_size == 2642 and q.yes_ask_size == 2902
        assert q.volume == 135 and q.open_interest == 135
        assert q.provider_timestamp is not None
        assert q.rules_hash is not None

    def test_quote_row_parses_dollars_only_payload(self):
        m = {k: v for k, v in self.CURRENT.items()
             if k not in ("yes_bid", "yes_ask")}
        q = markets._quote_row(m, mc_id=1, obs_id=1)
        assert (q.yes_bid_c, q.yes_ask_c) == (54, 55)

    def test_depth_parses_orderbook_fp_and_legacy(self):
        fp = {"orderbook_fp": {
            "yes_dollars": [["0.5400", "2642.00"], ["0.5300", "100"]],
            "no_dollars": [["0.4500", "9.00"]]}}
        rows = markets._depth_levels(fp)
        # derived cents/size AND the exact provider strings retained (F7)
        assert ("yes", 54, 2642, "0.5400", "2642.00") in rows
        assert ("no", 45, 9, "0.4500", "9.00") in rows
        legacy = {"orderbook": {"yes": [[54, 2642]], "no": [[45, 9]]}}
        assert markets._depth_levels(legacy) == [
            ("yes", 54, 2642, None, None), ("no", 45, 9, None, None)]
        assert markets._depth_levels({}) == []

    def test_quote_row_retains_exact_fixed_point(self):
        """V9 eval F7: subpenny prices and fractional sizes are kept
        beside the derived integer cents, never rounded away at ingest."""
        m = {"yes_bid": 2, "yes_bid_dollars": "0.0150",
             "yes_ask_dollars": "0.0175", "yes_bid_size_fp": "13.50",
             "volume_fp": "42.00"}
        q = markets._quote_row(m, mc_id=1, obs_id=1)
        assert q.yes_bid_c == 2                     # derived (rounded)
        assert q.yes_bid_dollars == "0.0150"        # exact subpenny
        assert q.yes_ask_dollars == "0.0175"
        import json as _json
        sizes = _json.loads(q.sizes_fp_json)
        assert sizes["yes_bid_size"] == "13.50"     # exact fractional
        assert sizes["volume"] == "42.00"
        assert q.provider_precision


def _roster_summary(home_confirmed=True, away_confirmed=True,
                    home_gk=True):
    def team(side, confirmed, gk):
        roster = []
        if confirmed:
            roster.append({"starter": True, "jersey": "1",
                           "athlete": {"id": f"{side}gk", "displayName": f"{side} keeper"},
                           "position": {"abbreviation": "G" if gk else "D"}})
            for i in range(10):
                roster.append({"starter": True, "jersey": str(i + 2),
                               "athlete": {"id": f"{side}{i}", "displayName": f"{side} p{i}"},
                               "position": {"abbreviation": "M"}})
        return {"homeAway": side, "formation": "4-3-3" if confirmed else None,
                "roster": roster}
    return {"rosters": [team("home", home_confirmed, home_gk),
                        team("away", away_confirmed, True)]}


class TestLineupPlane:
    def test_parse_confirmed_lineup(self):
        from src.live import lineups
        p = lineups.parse_lineup(_roster_summary())
        assert p["home"]["confirmed"] and p["home"]["starters"] == 11
        assert p["home"]["goalkeeper"]["name"] == "home keeper"
        assert p["home"]["formation"] == "4-3-3"
        q = lineups.lineup_quality(p)
        assert q["LINEUP_CONFIRMED"] and q["GOALKEEPER_CONFIRMED"]
        assert q["AVAILABILITY_COMPLETE"]

    def test_parse_pending_lineup_is_not_confidence(self):
        from src.live import lineups
        p = lineups.parse_lineup(_roster_summary(home_confirmed=False))
        assert not p["home"]["confirmed"]
        q = lineups.lineup_quality(p)
        # a half-announced slate must NOT read as confirmed/complete
        assert not q["LINEUP_CONFIRMED"]
        assert not q["AVAILABILITY_COMPLETE"]

    def test_capture_writes_snapshot_with_provenance(self, live_session):
        from src.live import identity, lineups
        from src.live.models import (Fixture, LineupEntry, LineupSnapshot,
                                     Player)
        identity.seed_teams(CANNED_ESPN)
        fx = Fixture(competition_slug="mls-2026", espn_event_id="5000",
                     current_kickoff_utc=datetime.now(UTC), status="pre")
        live_session.add(fx)
        live_session.commit()
        res = lineups.capture_lineup(fx.id, summary=_roster_summary())
        assert res["status"] == "confirmed"
        assert res["quality"]["LINEUP_CONFIRMED"]
        snap = live_session.query(LineupSnapshot).one()
        assert snap.provider == "espn" and snap.parser_version
        assert snap.source_observation_id is not None
        assert snap.home_gk_player_id is not None
        assert live_session.query(LineupEntry).count() == 22
        assert live_session.query(Player).count() == 22

    def test_fetch_failure_still_records_a_referenced_snapshot(
            self, live_session, monkeypatch):
        """V9 eval F2: a lineup fetch failure must not return None (which
        left the T-10 lock referencing a null lineup and failing its own
        audit). It records an explicit 'fetch_failed' snapshot instead."""
        import requests as _requests

        from src.live import lineups
        from src.live.models import Fixture, LineupSnapshot
        fx = Fixture(competition_slug="mls-2026", espn_event_id="5001",
                     current_kickoff_utc=datetime.now(UTC), status="pre")
        live_session.add(fx)
        live_session.commit()

        def _boom(*a, **kw):
            raise _requests.RequestException("dns")
        monkeypatch.setattr(lineups.requests, "get", _boom)
        res = lineups.capture_lineup(fx.id)          # summary=None -> fetch
        assert res is not None and res["status"] == "fetch_failed"
        snap = live_session.query(LineupSnapshot).one()
        assert snap.status == "fetch_failed"
        assert snap.source_observation_id is None    # no observation to link
        assert res["snapshot_id"] == snap.id         # lock can reference it
        assert res["quality"]["LINEUP_CONFIRMED"] is False


class TestPaperFillModel:
    def test_book_walk_partial_and_slippage(self):
        from src.live import paper
        # ladder: 20 @ 55c, 30 @ 56c, 40 @ 58c (best first)
        ladder = [(55, 20), (56, 30), (58, 40)]
        # request 100 but only 90 available -> partial
        r = paper.simulate_fill(ladder, 100)
        assert r["filled"] == 90
        assert r["best_ask_c"] == 55
        assert r["levels"] == 3
        # weighted avg = (20*55 + 30*56 + 40*58)/90
        assert r["avg_price_c"] == round((20*55 + 30*56 + 40*58) / 90)
        assert r["slippage_c"] == r["avg_price_c"] - 55
        # deep enough book -> full fill, no book exhaustion
        r2 = paper.simulate_fill(ladder, 20)
        assert r2["filled"] == 20 and r2["avg_price_c"] == 55
        assert r2["slippage_c"] == 0

    def test_yes_buy_ladder_from_no_depth(self):
        from src.live import paper
        from types import SimpleNamespace as NS
        # NO bids at 44,45 -> YES asks at 56,55 (best = 55)
        depth = [NS(side="no", price_c=44, size=30),
                 NS(side="no", price_c=45, size=20),
                 NS(side="yes", price_c=54, size=99)]  # ignored for buys
        quote = NS(yes_ask_c=55, yes_ask_size=10)
        ladder = paper.yes_buy_ladder(quote, depth)
        assert ladder == [(55, 20), (56, 30)]     # best (lowest ask) first
        # no depth -> fall back to the top quote
        assert paper.yes_buy_ladder(quote, []) == [(55, 10)]


class TestPaperTrading:
    def _lock_with_book(self, live_session, ask=45, bid=44,
                        exec_ready=True, model_p=0.60):
        """A canonical lock whose game 3-way has a quote + depth, and a
        model probability we control, so the net edge is deterministic."""
        from src.live.models import (Competition, Fixture, MarketContract,
                                     MarketEvent, MarketDepthLevel,
                                     MarketQuote, MarketSnapshot,
                                     ModelVersion, PredictionContract,
                                     PredictionRun)
        s = live_session   # the fixture already seeded Competition
        s.add(ModelVersion(name=model_mls.MODEL_NAME,
                           approved_for_shadow=True))
        fx = Fixture(id=1, competition_slug="mls-2026",
                     espn_event_id="p1", status="pre",
                     current_kickoff_utc=datetime.now(UTC))
        snap = MarketSnapshot(id=1, fixture_id=1,
                              captured_at=datetime.now(UTC),
                              status="complete", execution_ready=exec_ready,
                              oldest_quote_age_seconds=30)
        ev = MarketEvent(id=1, competition_slug="mls-2026",
                         kalshi_event_ticker="KXMLSGAME-x", series="KXMLSGAME",
                         fixture_id=1, mapping_approved=True)
        mc = MarketContract(id=1, market_event_id=1,
                            ticker="KXMLSGAME-x-H", outcome_key="home_win")
        q = MarketQuote(id=1, market_contract_id=1,
                        captured_at=datetime.now(UTC),
                        yes_ask_c=ask, yes_bid_c=bid, yes_ask_size=50,
                        market_snapshot_id=1)
        run = PredictionRun(id="lock", fixture_id=1, run_type="t10",
                            status="complete", canonical=True,
                            market_snapshot_id=1)
        s.add_all([fx, snap, ev, mc, q, run])
        s.flush()
        # depth: NO bids -> a buyable YES ladder
        s.add_all([MarketDepthLevel(market_quote_id=1, side="no",
                                    price_c=100 - ask, size=30),
                   MarketDepthLevel(market_quote_id=1, side="no",
                                    price_c=100 - ask - 1, size=200)])
        s.add(PredictionContract(prediction_run_id="lock",
                                 market_contract_id=1,
                                 market_quote_id=1, outcome_key="home_win",
                                 raw_probability=model_p))
        for ok in ("draw", "away_win"):
            s.add(PredictionContract(prediction_run_id="lock",
                                     outcome_key=ok, raw_probability=0.2))
        s.commit()
        return fx

    def test_positive_edge_fills_deterministically(self, live_session,
                                                   monkeypatch):
        from src.live import paper
        from src.live.models import PaperFill, PaperSignal
        monkeypatch.setattr(config, "PAPER_TRADING_ENABLED", True)
        self._lock_with_book(live_session, ask=45, model_p=0.60)
        r1 = paper.paper_trade_lock("lock")
        assert r1["fills"] == 1
        fill = live_session.query(PaperFill).one()
        sig = live_session.query(PaperSignal).filter_by(
            decision="fill").one()
        # net edge = 0.60 - (0.45 + fee) > 0.03
        assert sig.net_edge > 0.03
        assert fill.filled_contracts == 100 and fill.best_ask_c == 45
        assert fill.cost_c > 0 and fill.fee_c > 0
        # idempotent + DETERMINISTIC replay: a second run adds nothing
        r2 = paper.paper_trade_lock("lock")
        assert r2["signals"] == 0
        assert live_session.query(PaperFill).count() == 1

    def test_gates_reject_with_reasons(self, live_session, monkeypatch):
        from src.live import paper
        from src.live.models import PaperFill, PaperSignal
        monkeypatch.setattr(config, "PAPER_TRADING_ENABLED", True)
        # model_p just above ask -> net edge below the 0.03 floor
        self._lock_with_book(live_session, ask=45, model_p=0.47)
        paper.paper_trade_lock("lock")
        sig = live_session.query(PaperSignal).filter_by(
            outcome_key="home_win").one()
        assert sig.decision == "reject"
        assert sig.reject_reason == "NET_EDGE_TOO_LOW"
        assert live_session.query(PaperFill).count() == 0

    def test_not_execution_ready_rejects(self, live_session, monkeypatch):
        from src.live import paper
        from src.live.models import PaperSignal
        monkeypatch.setattr(config, "PAPER_TRADING_ENABLED", True)
        self._lock_with_book(live_session, model_p=0.60, exec_ready=False)
        paper.paper_trade_lock("lock")
        sig = live_session.query(PaperSignal).filter_by(
            outcome_key="home_win").one()
        assert sig.decision == "reject"
        assert sig.reject_reason == "NOT_EXECUTION_READY"

    def test_settlement_pays_hits_and_records_pnl(self, live_session,
                                                  monkeypatch):
        from src.live import paper
        from src.live.models import Fixture, PaperFill
        monkeypatch.setattr(config, "PAPER_TRADING_ENABLED", True)
        fx = self._lock_with_book(live_session, ask=45, model_p=0.60)
        paper.paper_trade_lock("lock")
        # fixture finishes a home win -> the home_win bet hits
        fx.status = "post"; fx.home_goals = 2; fx.away_goals = 0
        live_session.commit()
        r = paper.settle_paper()
        assert r["settled"] == 1
        fill = live_session.query(PaperFill).one()
        assert fill.status == "settled" and fill.outcome_hit is True
        assert fill.payout_c == fill.filled_contracts * 100
        assert fill.pnl_c == fill.payout_c - fill.cost_c
        # settle is idempotent
        assert paper.settle_paper()["settled"] == 0


class TestRiskEngine:
    def test_correlation_groups_collapse_families(self):
        from src.live import risk
        for k in ("home_win", "home_margin_2", "home_team_over_1_5",
                  "home_first_goal"):
            assert risk.correlation_group(k) == "home"
        assert risk.correlation_group("away_win") == "away"
        assert risk.correlation_group("draw") == "draw"
        assert risk.correlation_group("over_2_5") == "over"

    def test_kill_switch_halts_everything(self, live_session, monkeypatch):
        from src.live import risk
        from types import SimpleNamespace as NS
        monkeypatch.setattr(config, "GLOBAL_TRADING_DISABLED", True)
        r = risk.exposure_gate(live_session, NS(id=1, home_team_id=1,
                                                away_team_id=2),
                               "home_win", 1000, 0)
        assert r == "KILL_SWITCH:GLOBAL_TRADING_DISABLED"

    def test_correlated_exposure_limit(self, live_session, monkeypatch):
        """Stacking correlated home bets on one match hits the shared
        match-direction budget before the per-match one."""
        from src.live import paper, risk
        from src.live.models import (Fixture, PaperFill, PaperSignal,
                                     PredictionRun)
        pol = risk.RISK_POLICY
        # an existing open home position near the correlated cap
        fx = Fixture(id=5, competition_slug="mls-2026", espn_event_id="r5",
                     home_team_id=1, away_team_id=2)
        run = PredictionRun(id="rr5", fixture_id=5, run_type="t10",
                            status="complete")
        live_session.add_all([fx, run])
        live_session.flush()
        sig = PaperSignal(fixture_id=5, outcome_key="home_win",
                          decision="fill", prediction_run_id="rr5")
        live_session.add(sig); live_session.flush()
        live_session.add(PaperFill(
            paper_signal_id=sig.id, status="open",
            cost_c=pol["max_correlated_exposure_c"] - 500))
        live_session.commit()
        # a new home_margin bet (same direction) that would exceed it
        r = risk.exposure_gate(live_session, fx, "home_margin_2", 1000, 0)
        assert r == "CORRELATED_EXPOSURE_LIMIT"
        # an AWAY bet (different direction) is not blocked by that budget
        r2 = risk.exposure_gate(live_session, fx, "away_win", 1000, 0)
        assert r2 != "CORRELATED_EXPOSURE_LIMIT"

    def test_paper_uses_risk_engine_for_exposure(self, live_session,
                                                 monkeypatch):
        """Paper trading rejects via the shared risk authority when a
        fill would blow the total-open cap."""
        from src.live import paper, risk
        from src.live.models import PaperSignal
        monkeypatch.setattr(config, "PAPER_TRADING_ENABLED", True)
        # shrink the total-open cap so one fill exceeds it
        monkeypatch.setitem(risk.RISK_POLICY, "max_total_open_c", 100)
        TestPaperTrading()._lock_with_book(live_session, ask=45,
                                           model_p=0.60)
        paper.paper_trade_lock("lock")
        sig = live_session.query(PaperSignal).filter_by(
            outcome_key="home_win").one()
        assert sig.decision == "reject"
        assert sig.reject_reason in ("TOTAL_RISK_LIMIT", "BANKROLL_RESERVE")


class TestSlateReport:
    def test_classifies_every_state(self, live_session, monkeypatch):
        """Each fixture lands in exactly one state; the slate qualifies
        only when clean."""
        from src.live import slate
        from src.live.models import (Fixture, LineupSnapshot,
                                     MarketSnapshot, ModelApprovalDecision,
                                     ModelInputArtifact,
                                     ModelVersion, PredictionContract,
                                     PredictionRun)
        from zoneinfo import ZoneInfo
        import json as _json
        # anchor to real now so past/future classification is robust; a
        # RECENT-PAST matchday so the fixtures read as kicked off
        past = datetime.now(UTC) - timedelta(days=5)
        et = past.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        live_session.add(ModelVersion(id=1, name=model_mls.MODEL_NAME,
                                      approved_for_shadow=True))
        live_session.add(ModelApprovalDecision(
            id=1, model_version_id=1, model_version_name=model_mls.MODEL_NAME,
            approved_mode="shadow", approved=True, content_hash="dh1",
            created_at=past - timedelta(days=1)))

        def fx(fid, eid, ko, status="post"):
            live_session.add(Fixture(id=fid, competition_slug="mls-2026",
                                     espn_event_id=eid,
                                     current_kickoff_utc=ko, status=status))

        # MISSED: kicked off, shadow-touched (a scheduled run), no lock
        fx(2, "miss", past)
        live_session.add(PredictionRun(id="s2", fixture_id=2,
                                       run_type="scheduled",
                                       status="complete"))
        # LEGACY_UNSCORABLE: kicked off, NO runs at all
        fx(4, "legacy", past)
        # PASS: audit-clean canonical lock, execution-ready snapshot
        fx(3, "pass", past)
        cap = past - timedelta(minutes=8)
        live_session.add_all([
            ModelInputArtifact(
                id=1, schema_version="model-input-v2", content_hash="h",
                document_json='{"engine": {"signature_hash": "sig-test"}}'),
            MarketSnapshot(id=1, fixture_id=3, captured_at=cap,
                           status="complete", execution_ready=True,
                           policy_version="mls-lock-v1",
                           required_families_complete=True),
            LineupSnapshot(id=1, fixture_id=3, captured_at=cap,
                           status="confirmed")])
        live_session.flush()
        live_session.add(PredictionRun(
            id="lk3", fixture_id=3, run_type="t10", status="complete",
            canonical=True, captured_at=cap, seconds_before_kickoff=480,
            market_snapshot_id=1, model_version_id=1,
            model_approval_decision_id=1,
            model_approved_at_run=True, model_input_artifact_id=1,
            input_snapshot_hash="h", lineup_snapshot_id=1,
            simulation_seed=1,
            input_quality_json=_json.dumps({"TEAM_DATA_FRESH": True})))
        live_session.flush()
        for k, p in (("home_win", 0.5), ("draw", 0.2), ("away_win", 0.3)):
            live_session.add(PredictionContract(
                prediction_run_id="lk3", outcome_key=k, raw_probability=p))
        live_session.commit()

        rep = slate.slate_report(et)
        states = {r["espn_event_id"]: r["state"] for r in rep["rows"]}
        assert states["miss"] == "MISSED"
        assert states["legacy"] == "LEGACY_UNSCORABLE"
        assert states["pass"] == "PASS", [r for r in rep["rows"]
                                          if r["espn_event_id"] == "pass"]
        assert rep["qualification"]["no_duplicate_canonical_locks"]
        assert rep["qualification"]["no_post_kickoff_locks"]
        assert rep["clean_slate"] is True

    def test_pending_before_lock_window(self, live_session):
        from src.live import slate
        from src.live.models import Fixture
        from zoneinfo import ZoneInfo
        fut = datetime.now(UTC) + timedelta(days=3)
        et = fut.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        live_session.add(Fixture(id=1, competition_slug="mls-2026",
                                 espn_event_id="pend",
                                 current_kickoff_utc=fut, status="pre"))
        live_session.commit()
        rep = slate.slate_report(et)
        row = next(r for r in rep["rows"] if r["espn_event_id"] == "pend")
        assert row["state"] == "PENDING"

    def test_execution_not_ready_is_flagged_not_failed(self, live_session):
        """An audit-clean lock whose book simply wasn't tradeable is
        EXECUTION_NOT_READY — valid evidence, flagged, not a failure."""
        from src.live import slate
        from src.live.models import (Fixture, LineupSnapshot,
                                     MarketSnapshot, ModelApprovalDecision,
                                     ModelInputArtifact,
                                     ModelVersion, PredictionContract,
                                     PredictionRun)
        import json as _json
        base = datetime(2026, 8, 16, 23, 30, tzinfo=UTC)
        live_session.add_all([
            ModelVersion(id=1, name=model_mls.MODEL_NAME,
                         approved_for_shadow=True),
            ModelApprovalDecision(
                id=1, model_version_id=1,
                model_version_name=model_mls.MODEL_NAME,
                approved_mode="shadow", approved=True, content_hash="dh1",
                created_at=base - timedelta(days=1)),
            Fixture(id=7, competition_slug="mls-2026", espn_event_id="enr",
                    current_kickoff_utc=base + timedelta(minutes=5),
                    status="pre"),
            ModelInputArtifact(
                id=1, schema_version="model-input-v2", content_hash="h",
                document_json='{"engine": {"signature_hash": "sig-test"}}'),
            MarketSnapshot(id=2, fixture_id=7,
                           captured_at=base - timedelta(minutes=1),
                           status="complete", execution_ready=False,
                           policy_version="mls-lock-v1",
                           required_families_complete=True),
            LineupSnapshot(id=1, fixture_id=7, captured_at=base,
                           status="confirmed")])
        live_session.flush()
        live_session.add(PredictionRun(
            id="lk7", fixture_id=7, run_type="t10", status="complete",
            canonical=True, captured_at=base - timedelta(minutes=1),
            seconds_before_kickoff=360, market_snapshot_id=2,
            model_version_id=1, model_approval_decision_id=1,
            model_approved_at_run=True,
            model_input_artifact_id=1, input_snapshot_hash="h",
            lineup_snapshot_id=1, simulation_seed=1,
            input_quality_json=_json.dumps({"TEAM_DATA_FRESH": True})))
        live_session.flush()
        for k, p in (("home_win", 0.5), ("draw", 0.2), ("away_win", 0.3)):
            live_session.add(PredictionContract(
                prediction_run_id="lk7", outcome_key=k, raw_probability=p))
        live_session.commit()
        rep = slate.slate_report("20260816")
        row = next(r for r in rep["rows"] if r["espn_event_id"] == "enr")
        assert row["state"] == "EXECUTION_NOT_READY"


class TestObservability:
    def test_metrics_shape_and_lock_success(self, live_session,
                                            monkeypatch):
        from src.live import observability, risk
        from src.live.models import (Fixture, PredictionRun)
        # a kicked-off shadow-touched fixture WITHOUT a lock = a miss
        fx = Fixture(id=9, competition_slug="mls-2026", espn_event_id="m9",
                     status="post",
                     current_kickoff_utc=datetime.now(UTC)
                     - timedelta(hours=1))
        live_session.add(fx)
        live_session.add(PredictionRun(id="sr9", fixture_id=9,
                                       run_type="scheduled",
                                       status="complete"))
        live_session.commit()
        m = observability.metrics()
        assert set(m) >= {"data", "locks", "runs", "paper"}
        assert m["locks"]["kicked_off_shadow_fixtures"] >= 1
        assert m["locks"]["missed_locks"] >= 1
        assert m["locks"]["lock_success_rate"] is not None
        # risk assessment is well-formed
        r = risk.assess()
        assert r["policy_version"] == "risk-v1"
        assert "active_kill_switches" in r


class TestModelLadderEval:
    def test_analytic_3way_is_exact_and_normalized(self):
        from src.live import model_eval
        p = model_eval.analytic_3way(1.6, 1.2)
        assert abs(sum(p.values()) - 1.0) < 1e-9
        assert p["home_win"] > p["away_win"]     # higher home rate
        # equal rates -> home and away symmetric, draw sizeable
        q = model_eval.analytic_3way(1.3, 1.3)
        assert abs(q["home_win"] - q["away_win"]) < 1e-9
        assert q["draw"] > 0.2

    def test_analytic_is_deterministic_no_mc_noise(self):
        from src.live import model_eval
        a = model_eval.analytic_3way(1.7, 1.1)
        b = model_eval.analytic_3way(1.7, 1.1)
        assert a == b                            # exact, not sampled

    def test_ladder_eval_ranks_and_bounds_ci(self, live_session):
        """On a separable season, M2 should not be worse than M0, and
        every edge carries a bootstrap CI."""
        from src.live import identity, model_eval
        from src.live.models import Fixture, Team
        identity.seed_teams(CANNED_ESPN)
        ids = [t.id for t in live_session.query(Team).filter_by(
            competition_slug="mls-2026")]
        now = datetime.now(UTC)
        k = 0
        # a strong team (ids[0]) and a weak one (ids[1]) with signal
        for rnd in range(10):
            for a, b, hg, ag in ((0, 1, 3, 0), (2, 3, 1, 1),
                                 (0, 2, 2, 0), (1, 3, 0, 1),
                                 (3, 0, 0, 2), (2, 1, 2, 1)):
                k += 1
                live_session.add(Fixture(
                    competition_slug="mls-2026", espn_event_id=f"e{k}",
                    home_team_id=ids[a], away_team_id=ids[b],
                    current_kickoff_utc=now - timedelta(days=200 - k),
                    status="post", home_goals=hg, away_goals=ag))
        live_session.commit()
        rep = model_eval.evaluate_ladder(n_boot=300)
        assert rep["n_scored"] > 10
        assert set(rep["variants"]) == {"M0", "M1", "M2"}
        for name in ("M0", "M1", "M2"):
            assert 0 < rep["variants"][name]["log_loss"] < 5
        edge = rep["edges"]["M2_vs_M0"]
        assert "ci95" in edge and len(edge["ci95"]) == 2
        assert isinstance(edge["significant"], bool)
        # M2 (ratings+recency) should not lose to M0 (no team info) here
        assert rep["variants"]["M2"]["log_loss"] <= \
            rep["variants"]["M0"]["log_loss"] + 0.02

    def test_approval_record_never_exceeds_shadow(self, live_session):
        from src.live import model_eval
        rec = model_eval.approval_record(
            {"eval_version": "x", "n_scored": 5,
             "variants": {"M2": {"log_loss": 1.0, "brier": 0.6}},
             "edges": {"M2_vs_M0": {"delta_log_loss": 0.01}}})
        assert rec["approved_mode"] == "shadow"
        assert "NOT" in rec["approval_meaning"]
        assert any("prospective" in x for x in rec["limitations"])

    def test_shadow_approval_policy_reads_the_ci(self):
        """V9 eval F1: the gate is the confidence interval, not a bare
        point estimate — significantly-worse is refused, a CI spanning
        zero is approvable for SHADOW, too-few-scored is refused."""
        from src.live import model_eval as me
        worse = {"n_scored": 162, "edges": {"M2_vs_M0": {
            "delta_log_loss": -0.05, "ci95": [-0.09, -0.01],
            "significant": True}}}
        assert me.shadow_approval_policy(worse)[0] is False
        spans_zero = {"n_scored": 162, "edges": {"M2_vs_M0": {
            "delta_log_loss": 0.008, "ci95": [-0.012, 0.029],
            "significant": False}}}
        assert me.shadow_approval_policy(spans_zero)[0] is True
        assert me.shadow_approval_policy({"n_scored": 5})[0] is False

    def test_approval_decision_persisted_and_deduped(self, live_session,
                                                     monkeypatch):
        """V9 eval F1/F10: boot persists an IMMUTABLE CI-based decision,
        sets approved_for_shadow FROM it, and an unchanged evaluation
        dedupes to one row — no bare point-estimate gate anywhere."""
        from src.live import model_eval as me
        from src.live.models import ModelApprovalDecision, ModelVersion
        report = {
            "eval_version": "model-eval-v1", "n_scored": 162,
            "variants": {"M2": {"log_loss": 1.07, "brier": 0.647,
                                "rps": 0.232}},
            "edges": {"M2_vs_M0": {"delta_log_loss": 0.008,
                                   "ci95": [-0.012, 0.029],
                                   "significant": False}},
        }
        monkeypatch.setattr(me, "evaluate_ladder", lambda **kw: report)
        dec = me.ensure_approval_decision()
        assert dec["approved"] is True and dec["decision_id"]
        row = live_session.get(ModelApprovalDecision, dec["decision_id"])
        assert row.approved_mode == "shadow" and row.content_hash
        assert row.edge_json and row.policy_version
        live_session.expire_all()
        mv = live_session.query(ModelVersion).filter_by(
            name=model_mls.MODEL_NAME).one()
        assert mv.approved_for_shadow is True         # set FROM the decision
        assert mv.approved_for_real_money is False     # never here
        # unchanged evaluation -> same content hash -> ONE immutable row
        assert (me.ensure_approval_decision()["decision_id"]
                == dec["decision_id"])
        assert live_session.query(ModelApprovalDecision).count() == 1


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


class TestContractRepair:
    def test_null_outcome_keys_heal_once_mapping_lands(self, live_session,
                                                       monkeypatch):
        """An event discovered before its fixture existed has label-only
        contracts; once mapped, _ensure_contracts must repair them."""
        from src.live.models import MarketContract, MarketEvent
        identity.seed_teams(CANNED_ESPN)
        teams = {t.canonical_name: t.id for t in
                 live_session.query(Team).filter_by(
                     competition_slug="mls-2026")}
        fx = Fixture(competition_slug="mls-2026", espn_event_id="801",
                     home_team_id=teams["Columbus Crew"],
                     away_team_id=teams["New York City FC"],
                     current_kickoff_utc=datetime(2026, 7, 25, 23, 30,
                                                  tzinfo=UTC),
                     status="pre")
        live_session.add(fx)
        live_session.flush()
        ev = MarketEvent(competition_slug="mls-2026",
                         kalshi_event_ticker="KXMLSGAME-26JUL25CLBNYC",
                         series="KXMLSGAME", title="Columbus vs New York City",
                         fixture_id=fx.id, mapping_approved=True,
                         mapped_via="alias")
        live_session.add(ev)
        live_session.flush()
        # the pre-mapping state: only Tie resolvable
        for ticker, label, okey in (
                ("KXMLSGAME-26JUL25CLBNYC-CLB", "Columbus", None),
                ("KXMLSGAME-26JUL25CLBNYC-NYC", "New York City", None),
                ("KXMLSGAME-26JUL25CLBNYC-TIE", "Tie", "draw")):
            live_session.add(MarketContract(
                market_event_id=ev.id, ticker=ticker,
                side_label=label, outcome_key=okey))
        live_session.commit()
        monkeypatch.setattr(markets, "_kalshi_get", lambda url, **kw: {
            "markets": [
                {"ticker": "KXMLSGAME-26JUL25CLBNYC-CLB",
                 "yes_sub_title": "Columbus"},
                {"ticker": "KXMLSGAME-26JUL25CLBNYC-NYC",
                 "yes_sub_title": "New York City"},
                {"ticker": "KXMLSGAME-26JUL25CLBNYC-TIE",
                 "yes_sub_title": "Tie"},
            ]})
        markets._ensure_contracts(live_session, ev)
        live_session.commit()
        keys = {c.ticker: c.outcome_key for c in
                live_session.query(MarketContract).filter_by(
                    market_event_id=ev.id)}
        assert keys["KXMLSGAME-26JUL25CLBNYC-CLB"] == "home_win"
        assert keys["KXMLSGAME-26JUL25CLBNYC-NYC"] == "away_win"
        assert keys["KXMLSGAME-26JUL25CLBNYC-TIE"] == "draw"


class TestPredictionRuns:
    def _seed_playable(self, s, n_completed=12):
        from src.live.models import ModelApprovalDecision, ModelVersion
        # run paths enforce the shadow-approval gate (V8 eval F3). Commit
        # before identity.seed_teams (which uses a SEPARATE session): an
        # uncommitted write here would hold the SQLite write lock and the
        # seed would fail "database is locked".
        mv = ModelVersion(name=model_mls.MODEL_NAME, approved_for_shadow=True)
        s.add(mv)
        s.commit()
        # the immutable approval decision every canonical lock must
        # reference (V9 eval F1/F10) — created before any run so it
        # precedes captured_at
        s.add(ModelApprovalDecision(
            model_version_id=mv.id, model_version_name=model_mls.MODEL_NAME,
            eval_version="model-eval-v1", policy_version="shadow-approval-v1",
            approved_mode="shadow", approved=True, n_scored=162,
            edge_json=('{"delta_log_loss": 0.008, '
                       '"ci95": [-0.012, 0.029], "significant": false}'),
            content_hash="test-decision-hash-0001",
            created_at=datetime.now(UTC) - timedelta(days=1)))
        s.commit()
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
        # the hub payload carries provenance AND the frozen display
        # extras (xg/scorelines/props travel WITH the stored run)
        hub = runs.model_for_event("9001")
        assert hub["shadow"] is True
        assert hub["latest"]["seed"] == model_mls.seed_for(
            live_session.query(Fixture).filter_by(
                espn_event_id="9001").one(), "scheduled")
        assert hub["latest"]["xg"]["home"] > 0
        assert len(hub["latest"]["scorelines"]) > 0
        assert "over_2_5" in hub["latest"]["props"]

    def test_incomplete_runs_are_invisible(self, live_session):
        up = self._seed_playable(live_session)
        live_session.add(PredictionRun(
            id="w-1", fixture_id=up.id, run_type="scheduled",
            status="writing", captured_at=datetime.now(UTC)))
        live_session.commit()
        assert runs.latest_odds() == []          # writing != complete
        assert runs.model_for_event("9001") is None

    def _fake_snapshot(self, s, fixture_id):
        from src.live.models import MarketSnapshot
        snap = MarketSnapshot(fixture_id=fixture_id,
                              captured_at=datetime.now(UTC),
                              status="complete", quotes_written=3)
        s.add(snap)
        s.commit()
        return {"snapshot_id": snap.id, "quote_by_ticker": {}}

    def test_t10_lock_is_canonical_and_single(self, live_session,
                                              monkeypatch):
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        up.current_kickoff_utc = datetime.now(UTC) + timedelta(minutes=9)
        live_session.commit()
        sent = []
        snap = self._fake_snapshot(live_session, up.id)
        monkeypatch.setattr(markets, "capture_lock_snapshot",
                            lambda fixture_id: snap)
        import src.alerts as alerts
        monkeypatch.setattr(alerts, "send_alert",
                            lambda msg, **kw: sent.append(msg))
        import src.live.lineups as lineups_mod
        monkeypatch.setattr(
            lineups_mod, "capture_lineup",
            lambda fixture_id, **kw: {
                "snapshot_id": 77, "status": "pending",
                "quality": {"LINEUP_CONFIRMED": False,
                            "GOALKEEPER_CONFIRMED": False,
                            "AVAILABILITY_COMPLETE": False,
                            "PLAYER_DATA_FRESH": False}})
        assert runs.t10_locks()["locked"] == 1
        assert runs.t10_locks()["locked"] == 0        # already locked
        assert len(sent) == 1 and "PAPER" in sent[0]
        lock = live_session.query(PredictionRun).filter_by(
            run_type="t10", canonical=True).one()
        assert lock.status == "complete"
        # provenance frozen with the run (V8 eval F2 + Phase 5)
        assert lock.market_snapshot_id == snap["snapshot_id"]
        assert lock.model_version_id is not None
        assert lock.input_snapshot_hash is not None
        assert lock.lineup_snapshot_id == 77
        # a PENDING lineup is recorded honestly, never absorbed as truth
        import json as _json
        iq = _json.loads(lock.input_quality_json)
        assert iq["LINEUP_CONFIRMED"] is False
        assert iq["TEAM_DATA_FRESH"] is True
        assert runs.model_for_event("9001")["t10_lock"] is not None

    def test_no_snapshot_means_no_canonical_lock(self, live_session,
                                                 monkeypatch):
        """THE V8-evaluation acceptance test: market capture failing or
        returning zero quotes must never produce a canonical lock."""
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        up.current_kickoff_utc = datetime.now(UTC) + timedelta(minutes=9)
        live_session.commit()
        monkeypatch.setattr(markets, "capture_lock_snapshot",
                            lambda fixture_id: None)
        assert runs.t10_locks()["locked"] == 0
        assert (live_session.query(PredictionRun)
                .filter_by(run_type="t10").count() == 0)

    def test_unapproved_model_cannot_run(self, live_session,
                                         monkeypatch):
        """F3: without an approved ModelVersion, no runs and no locks."""
        from src.live.models import ModelVersion
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        self._seed_playable(live_session)
        live_session.query(ModelVersion).update(
            {"approved_for_shadow": False})
        live_session.commit()
        assert "not approved" in runs.scheduled_runs()["skipped"]
        assert "not approved" in runs.t10_locks()["skipped"]
        assert (live_session.query(PredictionRun).count() == 0)

    def test_canonical_lock_is_primary_display(self, live_session,
                                               monkeypatch):
        """F9: a later scheduled run must not supersede the lock, and
        the sweep refuses to create one once the lock exists."""
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        up.current_kickoff_utc = datetime.now(UTC) + timedelta(minutes=9)
        live_session.commit()
        snap = self._fake_snapshot(live_session, up.id)
        monkeypatch.setattr(markets, "capture_lock_snapshot",
                            lambda fixture_id: snap)
        import src.alerts as alerts
        monkeypatch.setattr(alerts, "send_alert", lambda *a, **kw: None)
        assert runs.t10_locks()["locked"] == 1
        assert runs.scheduled_runs(freshness_hours=0.0)["created"] == 0
        hub = runs.model_for_event("9001")
        assert hub["primary"]["run_type"] == "t10"

    def test_lock_audit_passes_a_clean_lock(self, live_session,
                                            monkeypatch):
        """The acceptance audit reports a real snapshot-backed lock as
        all-pass, with retained failed snapshots and missed locks."""
        from src.live import audit
        from src.live.models import MarketSnapshot
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        up.current_kickoff_utc = datetime.now(UTC) + timedelta(minutes=9)
        live_session.commit()
        snap = self._fake_snapshot(live_session, up.id)
        # give the snapshot the manifest fields a clean lock needs
        row = live_session.get(MarketSnapshot, snap["snapshot_id"])
        row.policy_version = "mls-lock-v1"
        row.required_families_complete = True
        live_session.commit()
        monkeypatch.setattr(markets, "capture_lock_snapshot",
                            lambda fixture_id: snap)
        # hermetic: never touch live ESPN (the real capture_lineup makes a
        # network call; DNS-less CI otherwise produced a lineup-less lock)
        import src.live.lineups as lineups_mod
        monkeypatch.setattr(
            lineups_mod, "capture_lineup",
            lambda fixture_id, **kw: {
                "snapshot_id": 77, "status": "pending",
                "quality": {"LINEUP_CONFIRMED": False,
                            "GOALKEEPER_CONFIRMED": False,
                            "AVAILABILITY_COMPLETE": False,
                            "PLAYER_DATA_FRESH": False}})
        import src.alerts as alerts
        monkeypatch.setattr(alerts, "send_alert", lambda *a, **kw: None)
        assert runs.t10_locks()["locked"] == 1
        rep = audit.lock_audit()
        assert rep["summary"]["canonical_locks"] == 1
        lock = rep["locks"][0]
        assert lock["all_pass"], [k for k, v in lock["checks"].items()
                                  if not v]
        assert lock["checks"]["priced_contracts_quote_linked"]
        assert lock["checks"]["model_approved_at_run"]
        assert lock["checks"]["no_post_kickoff_replacement"]
        # V9 pre-slate: the approval-decision reference is a REQUIRED lock
        # invariant, and the engine signature must be present
        assert lock["checks"]["approval_decision_referenced"]
        assert lock["checks"]["approval_decision_exists"]
        assert lock["checks"]["approval_decision_model_matches"]
        assert lock["checks"]["approval_decision_is_shadow"]
        assert lock["checks"]["approval_decision_precedes_run"]
        assert lock["checks"]["engine_signature_present"]
        assert lock["approval_decision_id"] is not None
        assert lock["approval_decision_hash"] == "test-decision-hash-0001"
        assert lock["engine_signature_hash"]
        # the report is content-hashed and stable for one DB state
        assert rep["content_hash"] == audit.lock_audit()["content_hash"]

    def test_lock_without_approval_decision_fails_audit(self, live_session,
                                                        monkeypatch):
        """V9 pre-slate: a canonical lock that does not reference the
        immutable approval decision must FAIL the audit — the reference is
        required, not informational."""
        from src.live import audit
        from src.live.models import (MarketSnapshot, ModelApprovalDecision,
                                     PredictionRun)
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        up.current_kickoff_utc = datetime.now(UTC) + timedelta(minutes=9)
        live_session.commit()
        snap = self._fake_snapshot(live_session, up.id)
        row = live_session.get(MarketSnapshot, snap["snapshot_id"])
        row.policy_version = "mls-lock-v1"
        row.required_families_complete = True
        live_session.commit()
        monkeypatch.setattr(markets, "capture_lock_snapshot",
                            lambda fixture_id: snap)
        import src.live.lineups as lineups_mod
        monkeypatch.setattr(lineups_mod, "capture_lineup",
                            lambda fixture_id, **kw: {"snapshot_id": 77,
                                                      "status": "pending",
                                                      "quality": {}})
        import src.alerts as alerts
        monkeypatch.setattr(alerts, "send_alert", lambda *a, **kw: None)
        assert runs.t10_locks()["locked"] == 1
        # sever the reference to simulate an unauthorized/legacy lock
        live_session.query(PredictionRun).filter_by(
            run_type="t10", canonical=True).update(
            {"model_approval_decision_id": None})
        live_session.commit()
        lock = audit.lock_audit()["locks"][0]
        assert not lock["all_pass"]
        assert lock["checks"]["approval_decision_referenced"] is False

    def test_lock_audit_retains_missed_locks(self, live_session,
                                             monkeypatch):
        """A kicked-off, shadow-touched fixture with no canonical lock is
        RETAINED as a missed lock, not silently dropped."""
        from src.live import audit
        from src.live.models import Fixture as F
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        self._seed_playable(live_session)
        # a scheduled run exists (shadow-touched) but the fixture then
        # kicked off with no lock
        runs.scheduled_runs()
        up = live_session.query(F).filter_by(espn_event_id="9001").one()
        up.current_kickoff_utc = datetime.now(UTC) - timedelta(minutes=5)
        live_session.commit()
        rep = audit.lock_audit()
        missed = [m for m in rep["missed_locks"]
                  if m["espn_event_id"] == "9001"]
        assert len(missed) == 1

    def test_run_contracts_cover_mapped_prop_markets(self, live_session,
                                                     monkeypatch):
        """A mapped totals contract must join the batch with the run's
        own stored probability (full-family locks, O6/O7)."""
        from src.live.models import MarketContract, MarketEvent
        import json as _json
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        up = self._seed_playable(live_session)
        ev = MarketEvent(competition_slug="mls-2026",
                         kalshi_event_ticker="KXMLSTOTAL-26JUL25CLBNYC",
                         series="KXMLSTOTAL", title="CLB vs NYC: Total",
                         fixture_id=up.id, mapping_approved=True,
                         mapped_via="suffix")
        live_session.add(ev)
        live_session.flush()
        mc = MarketContract(market_event_id=ev.id,
                            ticker="KXMLSTOTAL-26JUL25CLBNYC-3",
                            side_label="Over 2.5 goals scored",
                            outcome_key="over_2_5")
        live_session.add(mc)
        live_session.commit()
        assert runs.scheduled_runs()["created"] >= 1
        run = (live_session.query(PredictionRun)
               .filter_by(fixture_id=up.id, status="complete").one())
        from src.live.models import PredictionContract
        row = (live_session.query(PredictionContract)
               .filter_by(prediction_run_id=run.id,
                          market_contract_id=mc.id).one())
        stored = _json.loads(run.payload_json)
        assert row.outcome_key == "over_2_5"
        assert row.raw_probability == pytest.approx(
            stored["props"]["over_2_5"])

    def test_input_artifact_stored_and_linked(self, live_session,
                                              monkeypatch):
        """Every run stores its retrievable input document, hash-linked
        to the run (Phase 2)."""
        from src.live.models import ModelInputArtifact, PredictionRun
        monkeypatch.setattr(config, "N_SIMULATIONS", 400)
        self._seed_playable(live_session)
        runs.scheduled_runs()
        run = (live_session.query(PredictionRun)
               .filter_by(status="complete").first())
        assert run.model_input_artifact_id is not None
        art = live_session.get(ModelInputArtifact,
                               run.model_input_artifact_id)
        assert art.content_hash == run.input_snapshot_hash
        import json
        doc = json.loads(art.document_json)
        assert doc["schema_version"] == "model-input-v2"
        assert doc["team_ratings"]["home"] and doc["team_ratings"]["away"]
        assert doc["simulation"]["seed"] == run.simulation_seed
        assert len(doc["source_fixtures"]) >= 5
        # V9 eval F4: the engine signature is frozen INTO the artifact
        eng = doc["engine"]
        assert eng["signature_hash"] and "goal_dispersion_cv" in \
            eng["constants"]

    def test_artifact_hash_is_deterministic(self, live_session):
        """The hash (hence dedup) depends only on the inputs: same
        fixture + model + run_type => identical bytes and hash."""
        from src.live import model_mls
        from src.live.models import Fixture
        self._seed_playable(live_session)
        model = model_mls.current_model()
        f = live_session.query(Fixture).filter_by(
            espn_event_id="9001").one()
        _, c1, h1 = model_mls.build_input_artifact(f, model, "t10")
        _, c2, h2 = model_mls.build_input_artifact(f, model, "t10")
        assert c1 == c2 and h1 == h2
        # a different run_type changes the seed, hence the artifact
        _, _, h3 = model_mls.build_input_artifact(f, model, "scheduled")
        assert h3 != h1

    def test_run_is_replayable_from_artifact_alone(self, live_session,
                                                   monkeypatch):
        """THE Phase-2 acceptance test: replay from the stored document
        ALONE (no live ratings) reproduces the stored probabilities."""
        from src.live import audit
        from src.live.models import PredictionRun
        monkeypatch.setattr(config, "N_SIMULATIONS", 600)
        self._seed_playable(live_session)
        runs.scheduled_runs()
        run = (live_session.query(PredictionRun)
               .filter_by(status="complete").first())
        rep = audit.verify_replay(run.id)
        assert rep["replayable"], rep
        # deterministic: same seed + same inputs => essentially identical
        assert rep["max_delta"] < 1e-6
        # V9 pre-slate: the engine signature is surfaced and matches (same
        # process), and the artifact schema is v2
        assert rep["artifact_schema"] == "model-input-v2"
        assert rep["stored_engine_signature_hash"]
        assert (rep["stored_engine_signature_hash"]
                == rep["current_engine_signature_hash"])
        assert rep["engine_match"] is True

    def test_approval_reader_returns_stored_decision(self, live_session):
        """V9 pre-slate: the approval reader returns the STORED decision
        (never a recomputation), and approval_decision_missing when none."""
        from src.live import model_eval
        assert model_eval.current_approval_decision().get(
            "approval_decision_missing") is True
        self._seed_playable(live_session)      # seeds an approved decision
        d = model_eval.current_approval_decision()
        assert d["approved"] is True and d["approved_mode"] == "shadow"
        assert d["decision_id"] and d["content_hash"]
        assert d["ci_low"] == -0.012 and d["ci_high"] == 0.029
        assert d["edge_significant"] is False
        assert d["corpus_manifest_hash"] is None   # no published corpus yet

    def test_corpus_is_self_contained_and_replayable(self, live_session,
                                                     monkeypatch, tmp_path):
        """Phase 3 acceptance: export the corpus, then reproduce every
        run and verify integrity from the FILES ALONE — no DB access in
        the analyzer path."""
        from src.live import corpus
        monkeypatch.setattr(config, "N_SIMULATIONS", 500)
        self._seed_playable(live_session)
        runs.scheduled_runs()
        out = str(tmp_path / "corpus-v1")
        manifest = corpus.export_corpus(out, "mls-shadow-2026-test")
        assert manifest["schema_version"] == "corpus-v1"
        assert manifest["counts"]["prediction_runs"] >= 1
        assert manifest["counts"]["input_artifacts"] >= 1

        # --- from here, DB is off-limits: analyze the files only ---
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location(
            "analyze_corpus",
            os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "scripts", "analyze_corpus.py"))
        an = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(an)
        m = an._load(out, "manifest.json")
        assert an.verify_hashes(out, m) == []          # integrity clean
        rep = an.replay_report(out)
        assert rep["all_reproduced"] and rep["max_delta"] <= 1e-6
        # audit section carries the anti-survivorship-bias record
        aud = an._load(out, "audit.json")
        assert "missed_locks" in aud and "failed_snapshots" in aud

    def test_corpus_versions_are_immutable(self, live_session, tmp_path):
        from src.live import corpus
        self._seed_playable(live_session)
        runs.scheduled_runs()
        out = str(tmp_path / "corpus")
        corpus.export_corpus(out, "mls-shadow-2026-test")
        with pytest.raises(FileExistsError):
            corpus.export_corpus(out, "mls-shadow-2026-test")

    def test_published_corpus_is_frozen_and_reserved(self, live_session):
        """V9 eval F3: a PUBLISHED version is served from stored bytes,
        unchanged as the database grows, and re-publishing is refused."""
        from datetime import datetime, timedelta, timezone

        from src.live import corpus
        from src.live.models import Fixture
        self._seed_playable(live_session)
        runs.scheduled_runs()
        pub = corpus.publish_corpus("mls-shadow-2026-frozen")
        assert pub["published"] == "mls-shadow-2026-frozen"
        frozen_hash = pub["manifest_hash"]
        served = corpus.get_published("mls-shadow-2026-frozen")
        assert served["manifest_hash"] == frozen_hash

        # mutate the DB — a rebuild WOULD change the hash...
        live_session.add(Fixture(
            competition_slug="mls-2026", espn_event_id="zzz9",
            current_kickoff_utc=datetime.now(timezone.utc)
            + timedelta(days=1), status="pre"))
        live_session.commit()
        assert corpus.build_corpus(
            "mls-shadow-2026-frozen")["manifest"]["manifest_hash"] \
            != frozen_hash
        # ...but the PUBLISHED bytes are unchanged (served from storage)
        assert corpus.get_published(
            "mls-shadow-2026-frozen")["manifest_hash"] == frozen_hash
        # re-publishing the same label is refused (immutable)
        again = corpus.publish_corpus("mls-shadow-2026-frozen")
        assert "error" in again and "immutable" in again["error"]

    def test_shadow_counts_shape(self, live_session):
        self._seed_playable(live_session)
        c = runs.shadow_counts()
        assert c["teams"] == 4 and c["fixtures"] == 25
        assert c["completed_fixtures"] == 24 and c["t10_locks"] == 0
