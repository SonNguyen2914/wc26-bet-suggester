"""Live match-state tracking — the single source of truth for what's on the
scoreboard and what's finished.

Why this exists: API-Football's /fixtures?live=all is NOT a reliable "is this
match still going" signal. A match drops out of it during between-periods
breaks (90'->ET, ET->penalties) and within a minute or two of the final
whistle. Trusting it directly caused two bugs: matches in extra time
vanished from the scoreboard, and finished matches couldn't be frozen with
their final score before they disappeared.

The fix: every poll, snapshot each live match's state to the DB
(MatchLiveSnapshot). The scoreboard reads from the snapshot store with grace
windows, so it holds a match through short feed gaps. When a match that was
live is gone from the feed for longer than the gap grace, we freeze it as a
MatchResult (final score captured) and it moves from live -> FT -> past.

All of this shares the ONE cached /fixtures?live=all pull the feed already
makes, so it costs no extra API budget.
"""
from __future__ import annotations

import json
from datetime import timedelta

import config
from src import live_feed
from src.db import (MatchLiveSnapshot, MatchResult, SessionLocal, utcnow)
from src.schedule_data import load_schedule

# --- grace windows -------------------------------------------------------
# How long the scoreboard keeps showing a match that's temporarily gone from
# the live feed. Must comfortably exceed the longest between-periods break
# (halftime-before-ET + the ET->penalties gap can be ~20 min of "not live").
GAP_GRACE = timedelta(minutes=config.LIVE_GAP_GRACE_MINUTES)
# A match gone from the feed for longer than GAP_GRACE is treated as FINISHED.
# Its final score is frozen from the last snapshot.

# How long a finished match stays on the live scoreboard as an "FT" card
# before it drops off (it remains in Past matches forever).
FT_WINDOW = timedelta(minutes=config.LIVE_FT_WINDOW_MINUTES)

_FINISHED_STATUSES = {"FT", "AET", "PEN"}

# The live feed is polled for a match only within a tight window around
# kickoff, NOT across the full 96h knockout tracking window. This keeps the
# daily API-Football budget for when a match is actually live instead of
# draining it days ahead (a knockout match is "trackable" 96h out for Kalshi
# market pricing, but there's nothing live to fetch until it's about to start).
LIVE_POLL_LEAD = timedelta(minutes=config.LIVE_POLL_LEAD_MINUTES)
LIVE_POLL_TRAIL = timedelta(hours=config.TRACK_HOURS_AFTER_KICKOFF)


def should_poll_live(m, now) -> bool:
    """True only when a match is near kickoff or plausibly in progress — the
    gate for hitting the live feed. Separate from is_trackable (which spans the
    full knockout window for market tracking) so the feed poll stays tight."""
    if not m.fully_resolved:
        return False
    return m.kickoff - LIVE_POLL_LEAD <= now < m.kickoff + LIVE_POLL_TRAIL


def poll_live_state() -> dict:
    """Refresh live snapshots and freeze any matches that have ended. Called
    every poll from the scheduler; shares the cached live feed pull, so no
    extra API cost. Returns a small summary for logging.

    Logic per trackable match:
      - feed says LIVE now  -> upsert snapshot (fresh last_seen_at)
      - feed FINISHED now    -> freeze MatchResult immediately, clear snapshot
      - feed silent (gone)   -> if we have a recent snapshot within GAP_GRACE,
                                leave it (between-periods gap, hold the card);
                                if the snapshot is older than GAP_GRACE, the
                                match really ended off-feed -> freeze it.
    """
    updated = frozen = held = 0
    frozen_matches = []                 # capture closings AFTER the commit
    now = utcnow()
    with SessionLocal() as s:
        for m in load_schedule():
            if not should_poll_live(m, now):
                continue
            # already frozen? nothing to do.
            if s.get(MatchResult, m.match_id):
                continue

            state = live_feed.live_state_for(m.home, m.away)

            if state and state.get("is_finished"):
                _freeze(s, m.match_id, state)
                frozen += 1
                frozen_matches.append(m)
                continue

            if state and state.get("is_live"):
                _upsert_snapshot(s, m.match_id, state)
                updated += 1
                continue

            # feed silent for this match — gap or a finish we missed live.
            snap = s.get(MatchLiveSnapshot, m.match_id)
            if snap is not None:
                age = now - _aware(snap.last_seen_at)
                if age > GAP_GRACE:
                    # gone too long -> it ended off-feed; freeze from snapshot.
                    _freeze_from_snapshot(s, snap)
                    frozen += 1
                    frozen_matches.append(m)
                else:
                    held += 1  # within grace: keep showing last-known state
        s.commit()

    # Market-side counterpart of the T-10 model lock: snapshot every priced
    # market's closing/settlement state the moment the match freezes.
    # capture_closing_snapshot never raises and is idempotent, so a feed
    # hiccup here can't disturb the poll, and re-freezes can't duplicate.
    for m in frozen_matches:
        from src import research
        research.capture_closing_snapshot(m)
    # A fresh result should fill its bracket slot NOW, not on the next
    # 30-minute resolver tick (Spain resolved instantly tonight while
    # France waited on the timer — the semifinal sat half-named).
    if frozen_matches:
        try:
            from src.bracket import resolve_bracket
            resolve_bracket()
        except Exception as exc:
            print(f"[live-state] post-freeze bracket resolve failed: {exc}")

    return {"updated": updated, "frozen": frozen, "held": held}


def restore_missing_results() -> dict:
    """Self-heal after a DB wipe (the Railway container has no volume, so a
    deploy resets SQLite): any resolved match whose live-poll window has
    already closed but has no MatchResult is re-frozen from ESPN's dated
    scoreboard, and its closing snapshot re-captured (both idempotent).
    Runs once at scheduler boot; cheap when there is nothing to heal.

    Runs to a FIXPOINT, not a single pass: a knockout match's teams are
    placeholders until its FEEDER results exist, so on a fully wiped DB the
    first pass restores the feeders but must skip the match itself
    (`fully_resolved` is still False when it's visited). resolve_bracket()
    after each pass fills the newly-decided slots, and the next pass can
    then restore that match's own result. One extra pass per bracket round,
    so this converges in <=4 passes and the steady-state cost is one no-op
    pass. (Found live 2026-07-15: SF1's result was unhealable after a
    deploy — its QF feeders restored in the same pass that skipped it.)"""
    restored = 0
    for _pass in range(6):    # bracket depth + margin; each pass is cheap
        pass_restored = _restore_pass()
        restored += pass_restored
        try:
            from src.bracket import resolve_bracket
            resolve_bracket()
        except Exception as exc:
            print(f"[live-state] post-restore bracket resolve failed: {exc}")
        if pass_restored == 0:
            break
    return {"restored": restored}


def _restore_pass() -> int:
    """One sweep of the schedule; returns how many results were restored."""
    restored = 0
    now = utcnow()
    for m in load_schedule():
        if not m.fully_resolved or now < m.kickoff + LIVE_POLL_TRAIL:
            continue          # upcoming/in-window matches use the normal path
        with SessionLocal() as s:
            if s.get(MatchResult, m.match_id):
                continue
        state = None
        for delta in (0, -1, 1):   # ESPN buckets by US-Eastern date
            day = (m.kickoff + timedelta(days=delta)).strftime("%Y%m%d")
            state = live_feed._espn_state_for(
                m.home, m.away, want_finished=True, on_date=day)
            if state:
                break
        if not state:
            print(f"[live-state] restore: no finished ESPN state for "
                  f"{m.match_id}")
            continue
        with SessionLocal() as s:
            _freeze(s, m.match_id, state)
            s.commit()
            # Stamp the REAL finish time (~kickoff + 2h30), not "now": a
            # freshly-restored old result must not ride the scoreboard's
            # recently-finished grace window (nine FT cards flooded the
            # landing page after a wipe+boot).
            row = s.get(MatchResult, m.match_id)
            if row is not None:
                row.finished_at = m.kickoff + timedelta(hours=2, minutes=30)
                s.commit()
        restored += 1
        print(f"[live-state] restored result {m.match_id} from dated ESPN")
        from src import research
        research.capture_closing_snapshot(m)
    return restored


def _aware(dt):
    """SQLite may hand back naive datetimes; treat them as UTC for math."""
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _upsert_snapshot(s, match_id: str, state: dict) -> None:
    snap = s.get(MatchLiveSnapshot, match_id)
    if snap is None:
        snap = MatchLiveSnapshot(match_id=match_id)
        s.add(snap)
    snap.home = state["home_name"]
    snap.away = state["away_name"]
    snap.home_goals = state["home_goals"]
    snap.away_goals = state["away_goals"]
    snap.minutes_elapsed = state["minutes_elapsed"]
    snap.status_short = state["status_short"]
    snap.red_home = state["red_home"]
    snap.red_away = state["red_away"]
    snap.goals_json = json.dumps(state.get("goals_list", []))
    snap.last_seen_at = utcnow()


def _freeze(s, match_id: str, state: dict) -> None:
    """Freeze a MatchResult from a live 'finished' feed state."""
    _write_result(
        s, match_id,
        home=state["home_name"], away=state["away_name"],
        hg=state["home_goals"], ag=state["away_goals"],
        status=state["status_short"],
        red_home=state["red_home"], red_away=state["red_away"],
        goals=state.get("goals_list", []),
    )
    _drop_snapshot(s, match_id)


def _freeze_from_snapshot(s, snap: MatchLiveSnapshot) -> None:
    """Freeze from the last-seen snapshot (match vanished off-feed). Status is
    inferred: if the last-seen minute was into extra time, call it AET, else
    FT — a best-effort label; the score is what matters and it's exact."""
    status = "AET" if (snap.minutes_elapsed or 0) > 90 else "FT"
    _write_result(
        s, snap.match_id,
        home=snap.home, away=snap.away,
        hg=snap.home_goals, ag=snap.away_goals,
        status=status, red_home=snap.red_home, red_away=snap.red_away,
        goals=json.loads(snap.goals_json or "[]"),
    )
    _drop_snapshot(s, snap.match_id)


def _write_result(s, match_id, *, home, away, hg, ag, status,
                  red_home, red_away, goals) -> None:
    res = s.get(MatchResult, match_id)
    if res is None:
        res = MatchResult(match_id=match_id)
        s.add(res)
    res.home, res.away = home, away
    res.home_goals, res.away_goals = hg, ag
    res.status_short = status
    res.red_home, res.red_away = red_home, red_away
    res.goals_json = json.dumps(goals)
    res.finished_at = utcnow()


def _drop_snapshot(s, match_id: str) -> None:
    snap = s.get(MatchLiveSnapshot, match_id)
    if snap is not None:
        s.delete(snap)


# --- read side: what the scoreboard and past-matches show ----------------

def scoreboard_entries() -> list[dict]:
    """Matches to show on the live scoreboard: currently-live (from snapshots,
    so they survive feed gaps) plus recently-finished within the FT window,
    shown as FT cards. Ordered live-first, then most-recently-finished."""
    now = utcnow()
    out: list[dict] = []
    with SessionLocal() as s:
        # live / in-gap snapshots
        for snap in s.query(MatchLiveSnapshot).all():
            out.append({
                "match_id": snap.match_id,
                "home": snap.home, "away": snap.away,
                "home_goals": snap.home_goals, "away_goals": snap.away_goals,
                "minutes_elapsed": snap.minutes_elapsed,
                "status_short": snap.status_short,
                "red_home": snap.red_home, "red_away": snap.red_away,
                "goals_list": json.loads(snap.goals_json or "[]"),
                "is_finished": False,
                "_sort": (0, -(snap.minutes_elapsed or 0)),
            })
        # recently-finished -> FT cards within the window. Guarded by the
        # match's KICKOFF age too: finished_at is write-time metadata and a
        # restore/backfill can stamp it "now" — an old result must never
        # resurface as a just-finished card regardless of bookkeeping.
        cutoff = now - FT_WINDOW
        recent_kick = {m.match_id for m in load_schedule()
                       if now - m.kickoff <= timedelta(hours=8)}
        for res in (s.query(MatchResult)
                    .filter(MatchResult.finished_at >= cutoff).all()):
            if res.match_id not in recent_kick:
                continue
            out.append({
                "match_id": res.match_id,
                "home": res.home, "away": res.away,
                "home_goals": res.home_goals, "away_goals": res.away_goals,
                "minutes_elapsed": None,
                "status_short": res.status_short,   # FT | AET | PEN
                "red_home": res.red_home, "red_away": res.red_away,
                "goals_list": json.loads(res.goals_json or "[]"),
                "is_finished": True,
                "_sort": (1, -_aware(res.finished_at).timestamp()),
            })
    out.sort(key=lambda e: e["_sort"])
    for e in out:
        e.pop("_sort", None)
    return out


def past_matches(limit: int = 20) -> list[dict]:
    """Finished matches, most-recent first, for the Past matches section."""
    out: list[dict] = []
    with SessionLocal() as s:
        for res in (s.query(MatchResult)
                    .order_by(MatchResult.finished_at.desc())
                    .limit(limit).all()):
            out.append({
                "match_id": res.match_id,
                "home": res.home, "away": res.away,
                "home_goals": res.home_goals, "away_goals": res.away_goals,
                "status_short": res.status_short,
                "goals_list": json.loads(res.goals_json or "[]"),
                "finished_at": _aware(res.finished_at).isoformat(),
            })
    return out


def is_finished(match_id: str) -> bool:
    """True once a match has a frozen result — the ranking board uses this to
    drop a match's bets the instant it ends (not 4h later)."""
    with SessionLocal() as s:
        return s.get(MatchResult, match_id) is not None
