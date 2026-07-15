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


class TestRestoreFixpoint:
    """Regression for the SF1 heal blindspot (found live 2026-07-15): on a
    fully wiped DB, a knockout match whose FEEDERS were also wiped is
    visited while its teams are still placeholders and skipped — a single
    restore pass can never heal it. The fixpoint loop must: heal the QFs,
    resolve the SF slots, heal the SFs on the next pass, then resolve the
    FINAL."""

    # finished states keyed by (home, away) as the resolver names them
    _RESULTS = {
        ("Morocco", "France"): (0, 2),
        ("Spain", "Belgium"): (2, 1),
        ("Norway", "England"): (1, 2),
        ("Argentina", "Switzerland"): (3, 1),
        ("France", "Spain"): (0, 2),
        ("England", "Argentina"): (1, 2),
    }

    def setup_method(self):
        from datetime import timedelta

        from src.db import (MatchResult, MatchLiveSnapshot, SessionLocal,
                            init_db, utcnow)
        init_db()
        with SessionLocal() as s:
            s.query(MatchResult).delete()
            s.query(MatchLiveSnapshot).delete()
            s.commit()
        sd._SCHEDULE = None            # fresh placeholders, like a real boot
        # pin every involved kickoff safely outside the live-poll window so
        # the test never depends on the real clock vs real fixture dates
        self._kicks = {}
        for m in sd.load_schedule():
            self._kicks[m.match_id] = m.kickoff
            m.kickoff = utcnow() - timedelta(days=2)

        self._orig_espn = lf._espn_state_for

        def fake_espn(home, away, want_finished=False, on_date=None):
            r = self._RESULTS.get((home, away))
            if r is None:
                return None
            return {"home_name": home, "away_name": away,
                    "home_goals": r[0], "away_goals": r[1],
                    "minutes_elapsed": 90.0, "status_short": "FT",
                    "is_live": False, "is_finished": True,
                    "red_home": False, "red_away": False, "goals_list": []}
        lf._espn_state_for = fake_espn
        # the resolver's feed fallbacks must stay offline in tests: force
        # them to "no answer" so only frozen MatchResult rows resolve slots
        self._orig_live = lf.live_state_for
        self._orig_fin = lf.finished_state_for
        lf.live_state_for = lambda h, a: None
        lf.finished_state_for = lambda h, a: None

        from src import research
        self._orig_snap = research.capture_closing_snapshot
        research.capture_closing_snapshot = lambda m: None

    def teardown_method(self):
        from src.db import MatchResult, SessionLocal
        lf._espn_state_for = self._orig_espn
        lf.live_state_for = self._orig_live
        lf.finished_state_for = self._orig_fin
        from src import research
        research.capture_closing_snapshot = self._orig_snap
        for m in sd.load_schedule():
            if m.match_id in self._kicks:
                m.kickoff = self._kicks[m.match_id]
        sd._SCHEDULE = None
        with SessionLocal() as s:
            s.query(MatchResult).delete()
            s.commit()

    def test_feeder_dependent_matches_heal_to_fixpoint(self):
        from src.db import MatchResult, SessionLocal

        r = ls.restore_missing_results()
        assert r["restored"] == 6          # 4 QFs + both SFs

        with SessionLocal() as s:
            sf1 = s.get(MatchResult, "SF1")
            sf2 = s.get(MatchResult, "SF2")
        assert sf1 is not None and (sf1.home_goals, sf1.away_goals) == (0, 2)
        assert sf2 is not None and (sf2.home_goals, sf2.away_goals) == (1, 2)

        final = sd.get_match("FINAL")
        assert final.fully_resolved
        assert (final.home, final.away) == ("Spain", "Argentina")

    def test_steady_state_is_one_cheap_pass(self):
        ls.restore_missing_results()       # heal everything
        calls = {"n": 0}
        orig = lf._espn_state_for

        def counting(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)
        lf._espn_state_for = counting
        try:
            r = ls.restore_missing_results()
        finally:
            lf._espn_state_for = orig
        assert r["restored"] == 0
        # exactly ONE pass: each still-missing match (8 unmocked R16 + FINAL
        # + 3rd-place) probes <=3 ESPN date buckets. Fixpoint churn (six
        # passes) would show ~6x this.
        assert calls["n"] <= 3 * 10
