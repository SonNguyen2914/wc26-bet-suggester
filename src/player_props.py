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
