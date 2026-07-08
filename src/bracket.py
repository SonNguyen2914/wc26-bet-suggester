"""Bracket auto-resolution — fill QF placeholder slots as R16 results land.

The quarter-final slots in `schedule_data` are seeded with placeholder team
names ("USA/BEL winner") and feeder match_ids. This module reads the finished
Round-of-16 results from the live feed and swaps each placeholder for the real
winner, the moment that feeder match is decided.

Design mirrors the rest of the live layer:
  - budget-disciplined: reuses live_feed's ONE shared /fixtures?live=all pull
    when possible, and otherwise makes at most one extra /fixtures?round call;
  - graceful: no key / over budget / feed error / match not final -> it simply
    does nothing and leaves the placeholders in place;
  - idempotent: re-running after everything's resolved is a no-op (no calls,
    no writes), so it's safe to schedule frequently and cheaply.

It resolves FIXTURES only. Team STATS stay hand-sourced in schedule_data — a
freshly-resolved team with no TEAM_STATS entry runs on _DEFAULT and is flagged
provisional by `schedule_data.provisional_teams()`. This module never invents
model inputs.
"""
from __future__ import annotations

from datetime import timezone

import config
from src import live_feed
from src.schedule_data import Match, load_schedule, resolve_side


def _feeder_slots() -> list[tuple[str, str, str]]:
    """Every still-unresolved (feeder_match_id, qf_match_id, side) triple.

    Empty once the bracket is fully known — the caller uses that to skip all
    feed work, so a resolved bracket costs zero API calls.
    """
    out: list[tuple[str, str, str]] = []
    for m in load_schedule():
        if not m.home_resolved:
            for f in m.home_feeders:
                out.append((f, m.match_id, "home"))
        if not m.away_resolved:
            for f in m.away_feeders:
                out.append((f, m.match_id, "away"))
    return out


def _winner_of(feeder: Match, state: dict) -> str | None:
    """Given a FINISHED feeder's live-feed state (already oriented to our
    home/away order), return the winning team name, or None if we can't tell
    (not finished, or a draw with no shootout info — knockouts always resolve,
    so a clean draw means the feed hasn't posted the ET/pens winner yet)."""
    if not state.get("is_finished"):
        return None
    hg = state.get("home_goals") or 0
    ag = state.get("away_goals") or 0
    if hg > ag:
        return feeder.home
    if ag > hg:
        return feeder.away
    # Level after the feed's reported score. For AET/PEN the aggregate goals
    # usually already reflect ET; a true tie here means penalties decided it
    # and we can't read the shootout from the goals field alone -> defer to
    # the next run (the feed's status/score settles shortly after).
    return None


def _feeder_result(feeder) -> dict | None:
    """The feeder's finished state, from the cheapest available source.
    Returns a state dict with home_goals/away_goals/is_finished, oriented to
    the feeder's home/away order, or None if we can't determine a result yet.
    """
    # 1. Frozen MatchResult (written by the live-state poller — no API cost).
    from src.db import MatchResult, SessionLocal
    with SessionLocal() as s:
        res = s.get(MatchResult, feeder.match_id)
        if res is not None:
            return {"home_name": res.home, "away_name": res.away,
                    "home_goals": res.home_goals, "away_goals": res.away_goals,
                    "is_finished": True, "status_short": res.status_short}
    # 2. Live feed — the match is finishing right now.
    state = live_feed.live_state_for(feeder.home, feeder.away)
    if state and state.get("is_finished"):
        return state
    # 3. Finished-fetch — the match finished in the past (days ago).
    return live_feed.finished_state_for(feeder.home, feeder.away)


def resolve_bracket() -> list[dict]:
    """Resolve every QF placeholder whose feeder has finished. Returns a list
    of {qf, side, team} dicts for what actually changed this run (so the caller
    can log/alert exactly once per resolution). Safe and cheap to call often.
    """
    slots = _feeder_slots()
    if not slots:
        return []  # bracket fully known — no feed work at all

    if not config.API_FOOTBALL_KEY:
        return []  # no feed configured; placeholders stay, UI shows "TBD"

    changed: list[dict] = []
    schedule = {m.match_id: m for m in load_schedule()}

    # De-dupe feeder lookups: several slots can't share a feeder, but a feeder
    # appears once per unresolved side it feeds — look each up at most once.
    seen_state: dict[str, dict | None] = {}
    for feeder_id, qf_id, side in slots:
        feeder = schedule.get(feeder_id)
        if feeder is None:
            continue
        if feeder_id not in seen_state:
            # Read the feeder's result from the best available source:
            #  1. MatchResult (frozen by the live-state poller — free, no API)
            #  2. live feed (the match is finishing right now)
            #  3. finished-fetch (the match finished in the past — one API
            #     call, cached for the day)
            # This makes resolution robust to WHEN it runs: a match that
            # finished days ago is long gone from live=all, so (1) and (3)
            # are what actually unstick the bracket after the fact.
            seen_state[feeder_id] = _feeder_result(feeder)
        state = seen_state[feeder_id]
        if not state:
            continue
        winner = _winner_of(feeder, state)
        if not winner:
            continue
        if resolve_side(qf_id, side, winner):
            changed.append({"qf": qf_id, "side": side, "team": winner,
                            "feeder": feeder_id})
            print(f"[bracket] {qf_id} {side} = {winner} "
                  f"(won {feeder_id})")

    return changed


def _win_probs(m) -> dict | None:
    """Model home/draw/away win probabilities for a resolved match, for the
    bracket view. Uses cached predictions if present, else runs a quick sim.
    Returns None for placeholder matches (no real teams yet)."""
    if not m.fully_resolved:
        return None
    try:
        from src.cache import latest_for_match
        snap = latest_for_match(m.match_id)
        if snap:
            probs = {}
            for mkt in snap.get("markets", []):
                k = mkt.get("outcome_key")
                if k in ("home_win", "draw", "away_win"):
                    probs[k] = mkt["model_probability"]
            if "home_win" in probs and "away_win" in probs:
                return probs
        # no cache — quick direct simulation
        from src.models.simulator import MatchSimulator
        from src.schedule_data import get_team_stats
        sim = MatchSimulator(n_simulations=8000, seed=7)
        r = sim.simulate(get_team_stats(m.home), get_team_stats(m.away),
                         stage="knockout")
        o = r["outcomes"]
        return {"home_win": o.get("home_win", 0.0),
                "draw": o.get("draw", 0.0),
                "away_win": o.get("away_win", 0.0)}
    except Exception:
        return None


def _bracket_match(m) -> dict:
    return {
        "match_id": m.match_id,
        "home": m.home, "home_resolved": m.home_resolved,
        "away": m.away, "away_resolved": m.away_resolved,
        "fully_resolved": m.fully_resolved,
        "kickoff": m.kickoff.astimezone(timezone.utc).isoformat(),
        "venue": m.venue,
        "stage": m.group,
        "probs": _win_probs(m),
    }


def bracket_status() -> dict:
    """Full knockout bracket for the UI: quarterfinals, semifinals, and (if
    seeded) the final, each with model win probabilities for resolved matches.
    Read-only, no feed calls (probs come from cache or a quick local sim)."""
    by_stage = {"QF": [], "SF": [], "F": []}
    for m in load_schedule():
        if m.group in by_stage:
            by_stage[m.group].append(_bracket_match(m))
    # stable order by kickoff within each round
    for k in by_stage:
        by_stage[k].sort(key=lambda x: x["kickoff"])
    return {
        "quarterfinals": by_stage["QF"],
        "semifinals": by_stage["SF"],
        "final": by_stage["F"],
    }
