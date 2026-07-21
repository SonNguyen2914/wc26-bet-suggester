"""Bracket auto-resolution: placeholders, feed-driven resolution, provisional
stats, and trackability gating."""
import config
import src.schedule_data as sd
import src.bracket as bracket


def _reset_schedule():
    sd._SCHEDULE = None
    sd.load_schedule()


class TestPlaceholders:
    def setup_method(self):
        _reset_schedule()

    def test_qf_slots_exist(self):
        qfs = [m for m in sd.load_schedule() if m.group == "QF"]
        assert len(qfs) == 4

    def test_qf1_fully_known(self):
        # Morocco vs France resolved before seeding (France beat Paraguay Jul 4)
        m = sd.get_match("MAR_FRA")
        assert m is not None
        assert m.fully_resolved
        assert m.home == "Morocco" and m.away == "France"

    def test_qfs_all_resolved(self):
        # All four QFs are seeded from confirmed R16 results.
        for mid in ("MAR_FRA", "ESP_BEL", "NOR_ENG", "ARG_SUI"):
            assert sd.get_match(mid).fully_resolved

    def test_sfs_are_placeholders(self):
        for mid in ("SF1", "SF2"):
            m = sd.get_match(mid)
            assert not m.fully_resolved
            assert "winner" in m.home or "winner" in m.away

    def test_placeholder_not_trackable(self):
        from src.db import utcnow
        m = sd.get_match("SF1")
        # even with an enormous window, an unresolved slot is not trackable
        assert not sd.is_trackable(m, utcnow(), 1e6, 1e6)

    def test_resolved_qf_is_trackable_in_window(self):
        from datetime import timedelta
        from src.db import utcnow
        m = sd.get_match("MAR_FRA")
        # a wide window around its real kickoff makes the resolved QF trackable
        assert sd.is_trackable(m, m.kickoff - timedelta(hours=1), 6, 6)


class TestResolution:
    def setup_method(self):
        _reset_schedule()
        self._key = config.API_FOOTBALL_KEY
        self._orig = bracket.live_feed.live_state_for

    def teardown_method(self):
        config.API_FOOTBALL_KEY = self._key
        bracket.live_feed.live_state_for = self._orig
        import importlib
        importlib.reload(bracket)

    def test_no_key_no_sources_is_noop(self):
        # The API-key gate is gone (V5): the resolver is key-agnostic and a
        # no-op only when NO source has a result. The feeder lookup must be
        # stubbed here — post-tournament every kickoff is in the past, so the
        # unstubbed fall-through reaches live ESPN and the test's outcome
        # rides on network luck (flaked in the Jul 21 audit: real results
        # exist now, so a successful fetch legitimately resolves the slot).
        config.API_FOOTBALL_KEY = ""
        bracket._feeder_result = lambda f: None
        assert bracket.resolve_bracket() == []
        assert not sd.get_match("SF1").fully_resolved

    def test_resolves_winner_from_result(self):
        config.API_FOOTBALL_KEY = "test"
        # SF1 home feeder is MAR_FRA. Feed a finished result: France won.
        finished = {"MAR_FRA":
                    {"home_name": "Morocco", "away_name": "France",
                     "home_goals": 0, "away_goals": 1, "is_finished": True}}
        bracket._feeder_result = lambda f: finished.get(f.match_id)
        changed = bracket.resolve_bracket()
        assert {"qf": "SF1", "side": "home", "team": "France",
                "feeder": "MAR_FRA"} in changed
        assert sd.get_match("SF1").home == "France"

    def test_unfinished_feeder_does_not_resolve(self):
        config.API_FOOTBALL_KEY = "test"
        # feeder not finished -> _feeder_result returns None -> no resolution
        bracket._feeder_result = lambda f: None
        assert bracket.resolve_bracket() == []
        assert not sd.get_match("SF1").home_resolved

    def test_draw_defers(self):
        # a level score with no shootout info can't pick a winner -> defer
        config.API_FOOTBALL_KEY = "test"
        drawn = {"NOR_ENG":
                 {"home_name": "Norway", "away_name": "England",
                  "home_goals": 1, "away_goals": 1, "is_finished": True}}
        bracket._feeder_result = lambda f: drawn.get(f.match_id)
        assert bracket.resolve_bracket() == []

    def test_idempotent(self):
        config.API_FOOTBALL_KEY = "test"
        finished = {"NOR_ENG":
                    {"home_name": "Norway", "away_name": "England",
                     "home_goals": 2, "away_goals": 0, "is_finished": True}}
        bracket._feeder_result = lambda f: finished.get(f.match_id)
        first = bracket.resolve_bracket()
        assert len(first) == 1
        assert bracket.resolve_bracket() == []  # no re-fire


class TestProvisional:
    def setup_method(self):
        _reset_schedule()

    def test_sourced_team_not_provisional(self):
        # Belgium has TEAM_STATS -> resolving it does NOT flag provisional
        sd.resolve_side("SF1", "home", "Belgium")
        assert "Belgium" not in sd.provisional_teams()

    def test_unsourced_team_is_provisional(self):
        # a made-up team with no stats entry -> flagged provisional
        sd.resolve_side("SF1", "away", "Wakanda")
        assert "Wakanda" in sd.provisional_teams()

    def test_placeholder_not_provisional(self):
        # unresolved placeholder text must never be treated as a real team
        prov = sd.provisional_teams()
        assert not any("winner" in t for t in prov)


class TestBracketStatus:
    def setup_method(self):
        from src.db import init_db
        init_db()
        _reset_schedule()

    def test_status_shape(self):
        st = bracket.bracket_status()
        assert len(st["quarterfinals"]) == 4
        assert len(st["semifinals"]) == 2
        assert len(st["third_place"]) == 1
        assert len(st["final"]) == 1
        assert "champion" in st
        row = next(q for q in st["quarterfinals"] if q["match_id"] == "MAR_FRA")
        assert row["fully_resolved"] is True
        assert "kickoff" in row and "venue" in row

    def test_third_place_is_loser_fed(self):
        from src.schedule_data import get_match
        assert get_match("THIRD").loser_feed is True


class TestPlaceholderNeverPersists:
    """run_for_match on an unresolved slot: no Kalshi fetch, no Prediction
    rows, no suggestions.

    Regression (prod 2026-07-13): a request that started on placeholder
    names simulated both sides on _DEFAULT stats, then the bracket resolver
    mutated the Match during the slow first Kalshi events fetch — market
    matching succeeded with the real names and the symmetric batch was
    persisted as the newest cache entry (SF2 xg 1.398/1.398 against 47 real
    markets, 8 seconds after the boot prime wrote the correct batch)."""

    def setup_method(self):
        _reset_schedule()

    def test_unresolved_slot_prices_and_persists_nothing(self):
        from src.db import Prediction, SessionLocal, init_db
        from src.suggester import SuggesterEngine

        init_db()
        with SessionLocal() as s:
            s.query(Prediction).filter(Prediction.match_id == "SF1").delete()
            s.commit()

        m = sd.get_match("SF1")
        assert not m.fully_resolved

        eng = SuggesterEngine()
        fetched: list[str] = []

        def _race_stub(match):
            # the resolver firing mid-request must not matter: an
            # unresolved-at-entry run never even reaches the fetch
            fetched.append(match.match_id)
            sd.resolve_side("SF1", "home", "France")
            sd.resolve_side("SF1", "away", "Spain")
            return [{"market_id": "KXWCGAME-X", "title": "France to win",
                     "outcome_key": "home_win", "yes_price": 0.5,
                     "decimal_odds": 2.0, "volume_24h": 99999.0}]

        eng.kalshi.get_markets_for_match = _race_stub
        result = eng.run_for_match(m, source="on_demand")

        assert fetched == [], "placeholder slot must never fetch markets"
        assert result["suggestions"] == []
        with SessionLocal() as s:
            n = (s.query(Prediction)
                 .filter(Prediction.match_id == "SF1").count())
        assert n == 0, "placeholder slot must never persist Prediction rows"

    def test_resolved_match_still_prices_and_persists(self):
        from src.db import Prediction, SessionLocal, init_db
        from src.suggester import SuggesterEngine

        init_db()
        with SessionLocal() as s:
            s.query(Prediction).filter(Prediction.match_id == "SF1").delete()
            s.commit()

        sd.resolve_side("SF1", "home", "France")
        sd.resolve_side("SF1", "away", "Spain")
        m = sd.get_match("SF1")
        assert m.fully_resolved

        eng = SuggesterEngine()
        eng.kalshi.get_markets_for_match = lambda match: [
            {"market_id": "KXWCGAME-X", "title": "France to win",
             "outcome_key": "home_win", "yes_price": 0.5,
             "decimal_odds": 2.0, "volume_24h": 99999.0}]
        result = eng.run_for_match(m, source="on_demand")

        assert len(result["suggestions"]) == 1
        with SessionLocal() as s:
            n = (s.query(Prediction)
                 .filter(Prediction.match_id == "SF1").count())
        assert n == 1
