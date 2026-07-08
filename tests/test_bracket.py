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

    def test_no_key_is_noop(self):
        config.API_FOOTBALL_KEY = ""
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
        _reset_schedule()

    def test_status_shape(self):
        st = bracket.bracket_status()
        assert len(st["quarterfinals"]) == 4
        row = next(q for q in st["quarterfinals"] if q["match_id"] == "MAR_FRA")
        assert row["fully_resolved"] is True
        assert "kickoff" in row and "venue" in row
