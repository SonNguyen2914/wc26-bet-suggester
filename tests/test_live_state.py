"""Live-state tracking: the ET-disappearance fix + finished-match handling.

Regression guard for the bug where a match in extra time vanished from the
scoreboard because API-Football drops it from /fixtures?live=all during
between-periods breaks.
"""
import config
from src.db import init_db
from src import live_state as ls
import src.schedule_data as sd
import src.live_feed as lf


def _live(hg, ag, minute, status, finished=False):
    return {"home_name": "Morocco", "away_name": "France",
            "home_goals": hg, "away_goals": ag, "minutes_elapsed": minute,
            "status_short": status, "is_live": not finished,
            "is_finished": finished, "red_home": False, "red_away": False,
            "goals_list": []}


class TestETDisappearance:
    def setup_method(self):
        init_db()
        # wipe any prior state for a clean per-test store
        from src.db import SessionLocal, MatchLiveSnapshot, MatchResult
        with SessionLocal() as s:
            s.query(MatchLiveSnapshot).delete()
            s.query(MatchResult).delete()
            s.commit()
        sd._SCHEDULE = None
        # poll_live_state gates on should_poll_live (a tight kickoff window);
        # force MAR_FRA "pollable" regardless of the real clock for these tests.
        self._orig = ls.should_poll_live
        ls.should_poll_live = lambda m, n: m.match_id == "MAR_FRA"
        self._origfeed = lf.live_state_for
        # The scoreboard refuses FT cards for matches that kicked off long
        # ago (the restore-flood guard), so these tests must not depend on
        # how far the real clock has drifted past MAR_FRA's actual kickoff:
        # pretend it kicked off two hours ago.
        from datetime import timedelta

        from src.db import utcnow
        self._m = sd.get_match("MAR_FRA")
        self._orig_kick = self._m.kickoff
        self._m.kickoff = utcnow() - timedelta(hours=2)

    def teardown_method(self):
        ls.should_poll_live = self._orig
        lf.live_state_for = self._origfeed
        self._m.kickoff = self._orig_kick

    def _feed(self, state):
        lf.live_state_for = lambda h, a: (
            state if {h, a} == {"Morocco", "France"} else None)

    def test_match_held_through_et_break(self):
        # live at 88'
        self._feed(_live(1, 1, 88.0, "2H"))
        ls.poll_live_state()
        assert any(e["match_id"] == "MAR_FRA"
                   for e in ls.scoreboard_entries())
        # feed goes silent (90'->ET break) — must NOT vanish
        lf.live_state_for = lambda h, a: None
        r = ls.poll_live_state()
        assert r["held"] == 1
        board = ls.scoreboard_entries()
        assert any(e["match_id"] == "MAR_FRA" for e in board), \
            "match vanished during the ET break — the bug"

    def test_et_status_shows(self):
        self._feed(_live(1, 1, 88.0, "2H"))
        ls.poll_live_state()
        self._feed(_live(1, 1, 105.0, "ET"))
        ls.poll_live_state()
        e = [x for x in ls.scoreboard_entries()
             if x["match_id"] == "MAR_FRA"][0]
        assert e["status_short"] == "ET"
        assert e["is_finished"] is False

    def test_finish_freezes_final_score(self):
        self._feed(_live(1, 1, 88.0, "2H"))
        ls.poll_live_state()
        self._feed(_live(2, 1, 120.0, "AET", finished=True))
        ls.poll_live_state()
        e = [x for x in ls.scoreboard_entries()
             if x["match_id"] == "MAR_FRA"][0]
        assert e["is_finished"] and e["status_short"] == "AET"
        assert (e["home_goals"], e["away_goals"]) == (2, 1)
        assert ls.is_finished("MAR_FRA")   # board drops bets
        past = ls.past_matches()
        assert past and past[0]["match_id"] == "MAR_FRA"

    def test_off_feed_finish_freezes_from_snapshot(self):
        # live in ET, then simply vanishes for good (feed never says FT)
        self._feed(_live(2, 1, 105.0, "ET"))
        ls.poll_live_state()
        lf.live_state_for = lambda h, a: None
        # force the snapshot to look old so grace expires
        from src.db import SessionLocal, MatchLiveSnapshot, utcnow
        from datetime import timedelta
        with SessionLocal() as s:
            snap = s.get(MatchLiveSnapshot, "MAR_FRA")
            snap.last_seen_at = utcnow() - timedelta(
                minutes=config.LIVE_GAP_GRACE_MINUTES + 5)
            s.commit()
        r = ls.poll_live_state()
        assert r["frozen"] == 1
        assert ls.is_finished("MAR_FRA")
        # frozen from snapshot: score preserved, status inferred AET (min>90)
        e = ls.past_matches()[0]
        assert (e["home_goals"], e["away_goals"]) == (2, 1)
        assert e["status_short"] == "AET"


class TestLivePollWindow:
    """Budget-drain regression: the live feed is polled only within a tight
    window around kickoff, NOT across the full 96h knockout tracking window."""

    def test_far_future_match_not_polled(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        far = type("M", (), {"fully_resolved": True,
                             "kickoff": now + timedelta(hours=48)})()
        assert ls.should_poll_live(far, now) is False

    def test_in_progress_match_polled(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        inplay = type("M", (), {"fully_resolved": True,
                                "kickoff": now - timedelta(minutes=30)})()
        assert ls.should_poll_live(inplay, now) is True

    def test_unresolved_placeholder_not_polled(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        unresolved = type("M", (), {"fully_resolved": False, "kickoff": now})()
        assert ls.should_poll_live(unresolved, now) is False
