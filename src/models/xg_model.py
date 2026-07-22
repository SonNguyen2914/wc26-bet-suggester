"""Expected-goals model.

v2 is an interpretable multiplicative model (the standard Dixon-Coles-style
starting point): a team's xG = league_base * own_attack * opponent_defence,
scaled by regressed form and fatigue, plus a CENTERED set-piece adjustment
(v1 added the full set-piece component on top of an attack rating that was
derived from total xG — double-counting the league's baseline set-piece
production; found by the Jul 21 independent evaluation).

Swap `predict_xg` for a trained XGBoost/NN later without touching the
simulator — the interface (two floats out) stays the same.
"""
from __future__ import annotations

from src.models.features import build_team_features

MODEL_VERSION = "v2-centered-setpiece"
LEAGUE_BASE_XG = 1.30  # average goals per team per WC match

# Competition-average set_piece_threat across the sourced TEAM_STATS table.
# `attack` is derived from TOTAL xG-for per game, which already contains
# set-piece production, so only a team's DEVIATION from the competition
# mean may move its xG — re-adding the baseline would count it twice.
# HONEST SCOPE (Jul 21 evaluation follow-up): centering eliminates the
# GLOBAL inflation but above/below-average set-piece variation is still
# represented twice (once inside total-xG attack, once here) — this
# MITIGATES the overlap, it does not fully remove it. Full removal needs
# decomposed inputs; note the extracted PMSR corpus carries set-play
# COUNTS (set_plays.csv), not set-piece xG, so decomposition requires
# re-extraction or a new source — next-competition work. A regression
# test pins this constant to the live stats table so drift is caught.
SET_PIECE_BASELINE = 0.236


def predict_xg(home_raw: dict, away_raw: dict) -> tuple[float, float]:
    home = build_team_features(home_raw)
    away = build_team_features(away_raw)

    # Open play: attack vs opposing defence, scaled by form + fatigue.
    # (Attack comes from total xGF, so this term already carries average
    # set-piece production — hence the centered adjustment below.)
    home_open = LEAGUE_BASE_XG * home["attack"] * away["defence"] \
        * (0.85 + 0.30 * home["form"]) * home["fatigue_mult"]
    away_open = LEAGUE_BASE_XG * away["attack"] * home["defence"] \
        * (0.85 + 0.30 * away["form"]) * away["fatigue_mult"]

    # Set pieces: deviation from the competition mean only.
    home_xg = home_open + (home["set_piece_threat"] - SET_PIECE_BASELINE)
    away_xg = away_open + (away["set_piece_threat"] - SET_PIECE_BASELINE)

    return (round(min(max(home_xg, 0.05), 4.0), 3),
            round(min(max(away_xg, 0.05), 4.0), 3))
