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


def _feeder_slots() -> list[tuple[str, str, str, bool]]:
    """Every still-unresolved (feeder_match_id, target_match_id, side,
    loser_feed) tuple. loser_feed=True means the slot is filled by the feeder's
    LOSER (the 3rd-place match) rather than its winner.

    Empty once the bracket is fully known — the caller uses that to skip all
    feed work, so a resolved bracket costs zero API calls.
    """
    out: list[tuple[str, str, str, bool]] = []
    for m in load_schedule():
        if not m.home_resolved:
            for f in m.home_feeders:
                out.append((f, m.match_id, "home", m.loser_feed))
        if not m.away_resolved:
            for f in m.away_feeders:
                out.append((f, m.match_id, "away", m.loser_feed))
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


def _loser_of(feeder: Match, state: dict) -> str | None:
    """The losing team (for the 3rd-place slot). Mirror of _winner_of."""
    if not state.get("is_finished"):
        return None
    hg = state.get("home_goals") or 0
    ag = state.get("away_goals") or 0
    if hg > ag:
        return feeder.away
    if ag > hg:
        return feeder.home
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
    # A feeder that hasn't kicked off yet can't have a result — skip the feed
    # entirely so the resolver spends zero budget before the match is played.
    from datetime import datetime, timezone
    if feeder.kickoff > datetime.now(timezone.utc):
        return None
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

    # No API-key gate here: the primary source is the frozen MatchResult row
    # (zero feed cost), and the feed fallbacks are ESPN-keyless-capable with
    # their own budgets. The old `if not API_FOOTBALL_KEY: return []` guard
    # predated the ESPN backbone and silently disabled DB-only resolution.

    changed: list[dict] = []
    schedule = {m.match_id: m for m in load_schedule()}

    # De-dupe feeder lookups: several slots can't share a feeder, but a feeder
    # appears once per unresolved side it feeds — look each up at most once.
    seen_state: dict[str, dict | None] = {}
    for feeder_id, qf_id, side, loser_feed in slots:
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
        # 3rd-place slots take the LOSER; everything else the winner.
        team = (_loser_of(feeder, state) if loser_feed
                else _winner_of(feeder, state))
        if not team:
            continue
        if resolve_side(qf_id, side, team):
            changed.append({"qf": qf_id, "side": side, "team": team,
                            "feeder": feeder_id})
            verb = "lost" if loser_feed else "won"
            print(f"[bracket] {qf_id} {side} = {team} ({verb} {feeder_id})")

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
                    # ship the edge alongside so the bracket UI can show it
                    # without fetching each match's full prediction (that
                    # per-viewer polling triggered fresh sims every 5 min).
                    if k == "home_win":
                        probs["home_edge"] = mkt.get("edge")
                    elif k == "away_win":
                        probs["away_edge"] = mkt.get("edge")
            if "home_win" in probs and "away_win" in probs:
                return probs
        # no cache — quick direct simulation (no market, so no edge)
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
    # If the match has finished, attach the final score + winner side so the
    # card can show the result (winner white, loser grey) instead of probs.
    result = None
    if m.fully_resolved:
        try:
            from src.db import MatchResult, SessionLocal
            with SessionLocal() as s:
                res = s.get(MatchResult, m.match_id)
                if res is not None:
                    if res.home_goals > res.away_goals:
                        winner = "home"
                    elif res.away_goals > res.home_goals:
                        winner = "away"
                    else:
                        winner = None
                    result = {
                        "home_goals": res.home_goals,
                        "away_goals": res.away_goals,
                        "status_short": res.status_short,
                        "winner": winner,
                    }
        except Exception:
            result = None  # DB not ready / no table — show as upcoming
    forecast = None
    if not m.fully_resolved:
        try:
            forecast = {
                "home": None if m.home_resolved else _side_forecast(m, "home"),
                "away": None if m.away_resolved else _side_forecast(m, "away"),
            }
        except Exception:
            forecast = None
    return {
        "match_id": m.match_id,
        "home": m.home, "home_resolved": m.home_resolved,
        "away": m.away, "away_resolved": m.away_resolved,
        "forecast": forecast,
        "fully_resolved": m.fully_resolved,
        "kickoff": m.kickoff.astimezone(timezone.utc).isoformat(),
        "venue": m.venue,
        "stage": m.group,
        # probs only for resolved-but-unfinished matches; finished shows score.
        "probs": _win_probs(m) if result is None else None,
        "result": result,
    }


def bracket_status() -> dict:
    """Full knockout bracket for the UI: quarterfinals, semifinals, 3rd-place,
    and final, each with model win probabilities (resolved-unfinished) or the
    final score (finished). Read-only, no feed calls."""
    by_stage = {"R16": [], "QF": [], "SF": [], "3P": [], "F": []}
    for m in load_schedule():
        if m.group in by_stage:
            by_stage[m.group].append(_bracket_match(m))
    for k in by_stage:
        by_stage[k].sort(key=lambda x: x["kickoff"])
    # R16 ordered by the QF each pair feeds (not kickoff), so the UI's tier
    # lines up feeders under their quarterfinal: QF1=MAR/FRA, QF2=ESP/BEL,
    # QF3=NOR/ENG, QF4=ARG/SUI.
    _r16_order = ["CAN_MAR", "PAR_FRA", "POR_ESP", "USA_BEL",
                  "BRA_NOR", "MEX_ENG", "ARG_EGY", "SUI_COL"]
    by_stage["R16"].sort(key=lambda x: (
        _r16_order.index(x["match_id"]) if x["match_id"] in _r16_order else 99))
    # Champion: the winner of the FINAL, once it's finished.
    champion = None
    for fm in by_stage["F"]:
        r = fm.get("result")
        if r and r.get("winner"):
            champion = fm["home"] if r["winner"] == "home" else fm["away"]
    # Model's champion forecast while the final is undecided.
    champion_forecast = None
    if champion is None:
        try:
            dist = _slot_dist("FINAL", "winner")
            if dist:
                t = max(dist, key=lambda x: dist[x])
                champion_forecast = {"team": t, "p": round(dist[t], 4)}
        except Exception:
            champion_forecast = None
    return {
        "champion_forecast": champion_forecast,
        "round_of_16": by_stage["R16"],
        "quarterfinals": by_stage["QF"],
        "semifinals": by_stage["SF"],
        "third_place": by_stage["3P"],
        "final": by_stage["F"],
        "champion": champion,
    }


# ---------------------------------------------------------------------------
# Model forecast for unresolved slots — recursive occupant distributions.
# "Who occupies this slot?" = finished result (probability 1) when frozen,
# else the pairwise-sim winner distribution; placeholder slots recurse into
# their feeders. Works identically at QF, SF and Final stage, so the bracket
# UI can always show the model's predicted semifinalists/finalists/champion.
# ---------------------------------------------------------------------------
def _slot_dist(match_id: str, want: str = "winner") -> dict:
    from src.player_props import _pairwise
    m = next((x for x in load_schedule() if x.match_id == match_id), None)
    if m is None:
        return {}
    if m.fully_resolved:
        # frozen result → certainty
        try:
            from src.db import MatchResult, SessionLocal
            with SessionLocal() as s:
                res = s.get(MatchResult, m.match_id)
            if res is not None and res.home_goals != res.away_goals:
                w = m.home if res.home_goals > res.away_goals else m.away
                l = m.away if w == m.home else m.home
                return {w: 1.0} if want == "winner" else {l: 1.0}
        except Exception:
            pass
        a, _ = _pairwise(m.home, m.away)
        return ({m.home: a, m.away: 1.0 - a} if want == "winner"
                else {m.home: 1.0 - a, m.away: a})
    dh = _slot_dist(m.home_feeders[0]) if m.home_feeders else {}
    da = _slot_dist(m.away_feeders[0]) if m.away_feeders else {}
    win: dict = {}
    for t, pt in dh.items():
        win[t] = win.get(t, 0.0) + pt * sum(
            po * _pairwise(t, o)[0] for o, po in da.items())
    for t, pt in da.items():
        win[t] = win.get(t, 0.0) + pt * sum(
            po * _pairwise(t, o)[0] for o, po in dh.items())
    if want == "winner":
        return win
    total = {**{t: p for t, p in dh.items()}, **{t: p for t, p in da.items()}}
    return {t: total[t] - win.get(t, 0.0) for t in total}


def _side_forecast(m, side: str) -> dict | None:
    feeders = m.home_feeders if side == "home" else m.away_feeders
    if not feeders:
        return None
    dist = _slot_dist(feeders[0], "loser" if m.loser_feed else "winner")
    if not dist:
        return None
    team = max(dist, key=lambda t: dist[t])
    return {"team": team, "p": round(dist[team], 4)}
