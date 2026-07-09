"""Player scoring props — Poisson thinning of the match simulation.

Every number traces to a source: team goal rates come from the SAME xG model
the match sim runs on (damped for knockouts), and each player's SHARE of his
team's scoring comes from FIFA Post-Match Summary Report distributions tables
(scripts/build_player_rates.py; 0.6·goal-share + 0.4·attempt-share,
normalised). Thinning a Poisson process is exact math, not a guess:

  lam_player            = lam_team · share
  P(anytime scorer)     = 1 − exp(−lam_player)
  P(scores match's 1st) = (lam_player / lam_total) · (1 − exp(−lam_total))

Honest limits (shown in the UI): 5-match samples; minutes/substitutions are
not modelled (a bench player's share reflects his tournament so far, not
tonight's likely minutes); knockout lineups can change. Kalshi's per-player
first-goal family (KXWCTEAMFIRSTGOAL) stays UNPRICED until its settlement
rules are verified — the 16.67x lesson.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import config

_RATES = Path(__file__).with_name("data") / "player_rates.json"


@lru_cache(maxsize=1)
def _rates() -> dict:
    return json.loads(_RATES.read_text())


def team_players(team: str) -> list[dict]:
    return _rates().get("teams", {}).get(team, [])


def props_for(home: str, away: str, stage: str,
              xg_home: float, xg_away: float, top_n: int = 10) -> dict:
    """Per-player anytime/first-goal probabilities for one match."""
    lam_h, lam_a = xg_home, xg_away
    if stage == "knockout":
        lam_h *= config.KNOCKOUT_DAMPING
        lam_a *= config.KNOCKOUT_DAMPING
    lam_tot = lam_h + lam_a
    p_any_goal = 1.0 - math.exp(-lam_tot) if lam_tot > 0 else 0.0

    def side(team: str, lam_team: float) -> list[dict]:
        out = []
        for p in team_players(team)[:top_n]:
            lam_p = lam_team * p["share"]
            out.append({
                "player": p["player"], "shirt": p["shirt"],
                "share": p["share"],
                "goals": p["goals"], "attempts": p["attempts"],
                "matches": p["matches"], "starts": p["starts"],
                "anytime": round(1.0 - math.exp(-lam_p), 4),
                **{k: v for k, v in goal_count_tails(lam_p).items()
                   if k in ("p2", "p3")},
                "first_goal": round((lam_p / lam_tot) * p_any_goal, 4)
                              if lam_tot > 0 else 0.0,
            })
        return out

    return {
        "home": side(home, lam_h),
        "away": side(away, lam_a),
        "p_no_goal": round(math.exp(-lam_tot), 4) if lam_tot > 0 else 1.0,
        "lambda": {"home": round(lam_h, 3), "away": round(lam_a, 3)},
        "source": _rates().get("source"),
        "share_model": _rates().get("share_model"),
    }


# ---------------------------------------------------------------------------
# Remaining-tournament anytime scorer — prices Kalshi's KXWCPLAYERGOALS
# ("Will X score a goal in the 2026 World Cup?"). For a player who hasn't
# scored yet, that is P(scores in any REMAINING match his team plays).
# Exact enumeration of the remaining bracket (QF pairs fixed; SF/F opponents
# are distributions over QF/SF winners) using pairwise advance probabilities
# and xG from the SAME simulator the rest of the site runs on.
# ---------------------------------------------------------------------------
_QF_PAIRS = [("Morocco", "France"), ("Spain", "Belgium"),
             ("Norway", "England"), ("Argentina", "Switzerland")]


@lru_cache(maxsize=64)
def _sim_pair(a: str, b: str) -> dict:
    from src.models.simulator import MatchSimulator
    from src.schedule_data import get_team_stats
    sim = MatchSimulator(n_simulations=20000, seed=11)
    return MatchSimulator and sim.simulate(get_team_stats(a),
                                           get_team_stats(b),
                                           stage="knockout")


def _pairwise(a: str, b: str) -> tuple[float, float]:
    """(P(a advances over b), a's damped goal rate vs b). The advance prob is
    computed once per UNORDERED pair (canonical order) so A(a,b) == 1-A(b,a)
    exactly — otherwise Monte-Carlo noise makes bracket paths sum to <1."""
    lam_a = _sim_pair(a, b)["xg"]["home"] * config.KNOCKOUT_DAMPING
    if a <= b:
        adv = _sim_pair(a, b)["advance"]["home"]
    else:
        adv = 1.0 - _sim_pair(b, a)["advance"]["home"]
    return adv, lam_a


def _bracket_paths(team: str):
    """Yield (probability, [opponents]) over the team's remaining run,
    enumerating every winner combination of the other slots."""
    qf_idx = next(i for i, p in enumerate(_QF_PAIRS) if team in p)
    my_qf_opp = _QF_PAIRS[qf_idx][1 - _QF_PAIRS[qf_idx].index(team)]
    partner_qf = _QF_PAIRS[qf_idx ^ 1]          # feeds the same SF
    other_side = [_QF_PAIRS[i] for i in ((2, 3) if qf_idx < 2 else (0, 1))]

    p_win_qf, _ = _pairwise(team, my_qf_opp)
    # lose QF: run ends after 1 match
    yield (1 - p_win_qf), [my_qf_opp]
    for sf_opp in partner_qf:
        p_sf_opp, _ = _pairwise(sf_opp, partner_qf[1 - partner_qf.index(sf_opp)])
        p_win_sf, _ = _pairwise(team, sf_opp)
        base = p_win_qf * p_sf_opp
        # lose SF: two matches played
        yield base * (1 - p_win_sf), [my_qf_opp, sf_opp]
        # reach the final: opponent = winner of the other side's mini-bracket
        for fa in other_side[0]:
            p_fa, _ = _pairwise(fa, other_side[0][1 - other_side[0].index(fa)])
            for fb in other_side[1]:
                p_fb, _ = _pairwise(fb, other_side[1][1 - other_side[1].index(fb)])
                for f_opp, pf in ((fa, None), (fb, None)):
                    p_f_opp, _ = _pairwise(
                        f_opp, fb if f_opp == fa else fa)
                    yield (base * p_win_sf * p_fa * p_fb * p_f_opp,
                           [my_qf_opp, sf_opp, f_opp])


def tournament_anytime(team: str, share: float) -> float:
    """P(player scores in his team's remaining tournament run)."""
    p_score = 0.0
    total = 0.0
    for prob, opps in _bracket_paths(team):
        p_none = 1.0
        for opp in opps:
            _, lam_team = _pairwise(team, opp)
            p_none *= math.exp(-lam_team * share)
        p_score += prob * (1.0 - p_none)
        total += prob
    return round(p_score / total, 4) if total > 0 else 0.0


def goal_count_tails(lam_p: float) -> dict:
    """P(player scores >=1 / >=2 / >=3) in one match — exact Poisson tails."""
    p0 = math.exp(-lam_p)
    p1 = p0 * lam_p
    p2 = p1 * lam_p / 2.0
    return {"p1": round(1 - p0, 4),
            "p2": round(1 - p0 - p1, 4),
            "p3": round(max(0.0, 1 - p0 - p1 - p2), 4)}


# ---------------------------------------------------------------------------
# Live Kalshi join — KXWCPLAYERGOALS ("Will X score a goal in the 2026 WC?")
# ---------------------------------------------------------------------------
import time
import unicodedata

import requests as _rq

from src.kalshi_client import (FIFA_CODES, _get_with_backoff,
                               _market_yes_price)

_pg_cache: dict = {}
_PG_TTL = 120


def _norm_name(s: str) -> str:
    n = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in n if c.isalpha()).upper()


def kalshi_player_markets(team: str) -> list[dict]:
    """Open KXWCPLAYERGOALS markets for a team (tournament anytime scorer).
    Public read; cached briefly; [] in demo mode / on any failure."""
    if config.DEMO_MODE:
        return []
    code = FIFA_CODES.get(team)
    if not code:
        return []
    hit = _pg_cache.get(code)
    if hit and time.time() - hit[0] < _PG_TTL:
        return hit[1]
    out: list[dict] = []
    try:
        s = _rq.Session()
        r = _get_with_backoff(
            s, f"{config.KALSHI_BASE_URL}/markets",
            {"event_ticker": f"KXWCPLAYERGOALS-26{code}", "limit": 100})
        for m in r.json().get("markets", []):
            price = _market_yes_price(m)
            out.append({
                "market_id": m.get("ticker"),
                "sub": m.get("yes_sub_title") or "",
                "norm": _norm_name(m.get("yes_sub_title") or ""),
                "yes_price": price,
            })
    except Exception as exc:                       # graceful: model-only
        print(f"[player-markets] {team} fetch failed: {exc}")
    _pg_cache[code] = (time.time(), out)
    return out


def join_markets(team: str, players: list[dict]) -> None:
    """Attach each player's Kalshi tournament-anytime market in place:
    market_id, implied, edge (anchored like every other market), multiplier.
    A player who already scored settles Yes — flagged, not priced."""
    mkts = kalshi_player_markets(team)
    by_norm = {m["norm"]: m for m in mkts if m["norm"]}
    for p in players:
        p["tournament_anytime"] = tournament_anytime(team, p["share"])
        p["already_scored"] = p["goals"] > 0
        mk = by_norm.get(_norm_name(p["player"]))
        if not mk or mk["yes_price"] is None:
            continue
        implied = mk["yes_price"]
        p["market_id"] = mk["market_id"]
        p["implied"] = round(implied, 4)
        p["multiplier"] = round(1.0 / implied, 2) if implied > 0.005 else None
        if p["already_scored"]:
            continue  # settles Yes; an "edge" here is meaningless
        anchored = (config.MODEL_WEIGHT * p["tournament_anytime"]
                    + (1 - config.MODEL_WEIGHT) * implied)
        p["likelihood"] = round(anchored, 4)
        p["edge"] = round(anchored - implied, 4)
