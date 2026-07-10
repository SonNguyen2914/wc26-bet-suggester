"""The self-running live read: state + shot stats -> levers -> market prices.

Every ~30s cycle (server-cached, so N viewers cost one run):
  1. live state from the snapshot store (already refreshed by the 15s tick)
  2. live shot stats from ESPN's boxscore (30s cache, keyless)
  3. attack levers DERIVED from the shots data — transparent and bounded:
     each side's live share of shots-on-target vs the share the pre-match
     xG implied, shrunk toward 1.0 early (weight = min/(min+45)) and capped
     to [0.75, 1.35] so one wild spell can't swing the simulation absurdly.
     Possession is deliberately ignored (Morocco out-passed France to an
     xG of 0.16 — ball-holding is not chance-creation).
  4. price_live() re-simulates the remainder and prices every open market;
     the Kalshi book list itself is cached ~75s (the sim moves much faster
     than the listings do).

Same honesty rules as the manual panel: the levers are ECHOED with their
full derivation, and live edge vs a market that already knows the score is
informational, never a TAKE signal.
"""
from __future__ import annotations

import json
import time

from src.db import MatchLiveSnapshot, SessionLocal
from src.live_feed import espn_match_stats

LEVER_CAP_LO, LEVER_CAP_HI = 0.75, 1.35
_SHRINK_MINUTES = 45.0          # at 45' the data has half the say

_markets_cache: dict[str, tuple[float, list]] = {}
_MARKETS_TTL = 75
_out_cache: dict[str, tuple[float, dict]] = {}
_OUT_TTL = 25


def suggest_levers(xg_home: float | None, xg_away: float | None,
                   stats: dict, minutes: float) -> dict:
    """Attack multipliers from live shots-on-target share vs the share the
    pre-match xG implied. Returns neutral levers with the reason when the
    inputs aren't there to justify anything else."""
    neutral = {"home": 1.0, "away": 1.0, "source": "neutral", "basis": None}
    if not stats.get("available") or not xg_home or not xg_away:
        return neutral
    rows = {r["key"]: r for r in stats.get("rows", [])}
    sot = rows.get("shotsOnTarget")
    shots = rows.get("totalShots")
    if not sot and not shots:
        return neutral

    def _num(row, side):
        try:
            return float(row[side]) if row else 0.0
        except (TypeError, ValueError):
            return 0.0

    # SoT is the signal; total shots joins at half weight. +1 smoothing so
    # tiny samples can't produce extreme shares.
    h = _num(sot, "home") + 0.5 * _num(shots, "home") + 1.0
    a = _num(sot, "away") + 0.5 * _num(shots, "away") + 1.0
    act_share = h / (h + a)
    exp_share = xg_home / (xg_home + xg_away)
    weight = max(0.0, min(1.0, minutes / (minutes + _SHRINK_MINUTES)))

    def lever(act, exp):
        raw = (act / exp) ** weight if exp > 0 else 1.0
        return round(max(LEVER_CAP_LO, min(LEVER_CAP_HI, raw)), 3)

    return {
        "home": lever(act_share, exp_share),
        "away": lever(1 - act_share, 1 - exp_share),
        "source": "live shots",
        "basis": {
            "sot_home": _num(sot, "home"), "sot_away": _num(sot, "away"),
            "shots_home": _num(shots, "home"), "shots_away": _num(shots, "away"),
            "actual_share_home": round(act_share, 3),
            "expected_share_home": round(exp_share, 3),
            "minutes": minutes, "weight": round(weight, 3),
            "cap": [LEVER_CAP_LO, LEVER_CAP_HI],
        },
    }


def _phase_from_status(short: str) -> str:
    if short in ("ET", "BT"):
        return "et"
    if short == "P":
        return "pens"
    return "regulation"


def live_auto(match, engine, prematch_xg: dict | None) -> dict:
    """The full auto cycle for one match. Cached ~25s."""
    hit = _out_cache.get(match.match_id)
    if hit and time.time() - hit[0] < _OUT_TTL:
        return hit[1]

    with SessionLocal() as s:
        snap = s.get(MatchLiveSnapshot, match.match_id)
        if snap is None:
            return {"available": False,
                    "reason": "no live snapshot — match not in progress"}
        state = {
            "home_goals": snap.home_goals, "away_goals": snap.away_goals,
            "minutes": float(snap.minutes_elapsed or 0.0),
            "red_home": int(snap.red_home or 0),
            "red_away": int(snap.red_away or 0),
            "status_short": snap.status_short or "",
            "goals_list": json.loads(snap.goals_json or "[]"),
        }

    stats = espn_match_stats(match.home, match.away)
    xg_h = (prematch_xg or {}).get("home")
    xg_a = (prematch_xg or {}).get("away")
    levers = suggest_levers(xg_h, xg_a, stats, state["minutes"])

    mhit = _markets_cache.get(match.match_id)
    if mhit and time.time() - mhit[0] < _MARKETS_TTL:
        markets = mhit[1]
    else:
        markets = engine.kalshi.get_markets_for_match(match)
        _markets_cache[match.match_id] = (time.time(), markets)

    priced = engine.price_live(
        match, state["home_goals"], state["away_goals"], state["minutes"],
        red_home=state["red_home"], red_away=state["red_away"],
        attack_home_mult=levers["home"], attack_away_mult=levers["away"],
        phase=_phase_from_status(state["status_short"]),
        markets=markets)

    out = {"available": True, **priced,
           "levers": levers,
           "status_short": state["status_short"],
           "goals_list": state["goals_list"],
           "stats_available": bool(stats.get("available"))}
    _out_cache[match.match_id] = (time.time(), out)
    return out
