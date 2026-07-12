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

# Defence (openness) lever: total live shot volume vs what the pre-match xG
# implied for the minutes played. The attack levers above only REDISTRIBUTE
# chances between the sides; this scales the whole goal environment — an
# end-to-end game raises both teams' conceding rates, a locked-down one
# lowers them. Capped tighter than attack: volume is a noisier signal.
DEF_CAP_LO, DEF_CAP_HI = 0.85, 1.20
# weighted shots (SoT + 0.5*shots) a team produces per 1.0 xG, roughly:
# ~12 shots / ~4.5 SoT for a 1.4-xG performance -> (4.5 + 6) / 1.4 ≈ 7.5,
# rounded up a touch for knockout long-shot inflation.
_SHOTS_PER_XG = 8.0

_markets_cache: dict[str, tuple[float, list]] = {}
_MARKETS_TTL = 75
_out_cache: dict[str, tuple[float, dict]] = {}
_OUT_TTL = 25


def suggest_levers(xg_home: float | None, xg_away: float | None,
                   stats: dict, minutes: float) -> dict:
    """Attack multipliers from live shots-on-target share vs the share the
    pre-match xG implied, plus symmetric DEFENCE multipliers from total shot
    volume vs expected (game openness). Returns neutral levers with the
    reason when the inputs aren't there to justify anything else."""
    neutral = {"home": 1.0, "away": 1.0, "def_home": 1.0, "def_away": 1.0,
               "source": "neutral", "basis": None}
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

    # openness: actual total weighted-shot volume vs the volume the xG
    # implied for the minutes played, shrunk by the same early-game weight.
    # Symmetric on both defences — volume says how open the GAME is, the
    # share levers above already say who is doing the creating.
    vol_actual = (h - 1.0) + (a - 1.0)          # strip the +1 smoothing
    vol_expected = _SHOTS_PER_XG * (xg_home + xg_away) * max(minutes, 1.0) / 90.0
    openness_raw = vol_actual / vol_expected if vol_expected > 0 else 1.0
    openness = round(max(DEF_CAP_LO, min(
        DEF_CAP_HI, openness_raw ** weight if openness_raw > 0 else 1.0)), 3)

    return {
        "home": lever(act_share, exp_share),
        "away": lever(1 - act_share, 1 - exp_share),
        "def_home": openness,
        "def_away": openness,
        "source": "live shots",
        "basis": {
            "sot_home": _num(sot, "home"), "sot_away": _num(sot, "away"),
            "shots_home": _num(shots, "home"), "shots_away": _num(shots, "away"),
            "actual_share_home": round(act_share, 3),
            "expected_share_home": round(exp_share, 3),
            "volume_actual": round(vol_actual, 1),
            "volume_expected": round(vol_expected, 1),
            "openness_raw": round(openness_raw, 3),
            "openness": openness,
            "minutes": minutes, "weight": round(weight, 3),
            "cap": [LEVER_CAP_LO, LEVER_CAP_HI],
            "def_cap": [DEF_CAP_LO, DEF_CAP_HI],
        },
    }


def sim_minutes(minutes: float, status_short: str) -> float:
    """The simulation wants MATCH PROGRESS, not the wall clock. 45'+5' of
    first-half stoppage is still only 45 of the 90 minutes played — feeding
    it 50 silently eats five minutes of the second half (and 90'+4' of 2H
    stoppage would wrongly read as extra time). Clamp per period.

    Stoppage endings: no API carries the fourth official's ANNOUNCED added
    time (feeds expose only the elapsed '+x'), so the end of a period can't
    be priced off a board number. Instead the feed itself is the evidence:
    while the status still says the period is RUNNING, a small remainder
    must stay on the clock — at 90'+4' the match is observably not over,
    and pricing it as finished is wrong (goals at 90+5 are real). The live
    tick refreshes that observation every 15s, so the floor self-expires
    the moment the feed flips to HT/FT."""
    s2 = (status_short or "").upper()
    if s2 == "1H":
        # deep in 1H stoppage: hold a sliver of the half open while it runs
        return min(minutes, 44.0) if minutes >= 45.0 else minutes
    if s2 == "HT":
        return 45.0                      # break: exactly half the match left
    if s2 == "2H":
        if minutes >= 90.0:
            return 88.0                  # still running -> ~2 min floor
        return max(minutes, 45.0)
    if s2 in ("ET", "BT"):
        if minutes >= 120.0:
            return 118.0
        return max(minutes, 90.0)
    if s2 == "P":
        return 120.0
    return minutes


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

    eff_minutes = sim_minutes(state["minutes"], state["status_short"])
    priced = engine.price_live(
        match, state["home_goals"], state["away_goals"], eff_minutes,
        red_home=state["red_home"], red_away=state["red_away"],
        attack_home_mult=levers["home"], attack_away_mult=levers["away"],
        defence_home_mult=levers.get("def_home", 1.0),
        defence_away_mult=levers.get("def_away", 1.0),
        phase=_phase_from_status(state["status_short"]),
        markets=markets,
        first_goal_scored=bool(state["goals_list"])
        or (state["home_goals"] + state["away_goals"]) > 0)

    out = {"available": True, **priced,
           "levers": levers,
           "status_short": state["status_short"],
           "goals_list": state["goals_list"],
           "stats_available": bool(stats.get("available"))}
    _out_cache[match.match_id] = (time.time(), out)
    return out
