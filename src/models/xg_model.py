"""Expected-goals model.

v1 is an interpretable multiplicative model (the standard Dixon-Coles-style
starting point): a team's xG = league_base * own_attack * opponent_defence,
scaled by regressed form and fatigue, plus a separate set-piece component.

Swap `predict_xg` for a trained XGBoost/NN later without touching the
simulator — the interface (two floats out) stays the same.
"""
from __future__ import annotations

from src.models.features import build_team_features

MODEL_VERSION = "v1-multiplicative"
LEAGUE_BASE_XG = 1.30  # average goals per team per WC match


def predict_xg(home_raw: dict, away_raw: dict) -> tuple[float, float]:
    home = build_team_features(home_raw)
    away = build_team_features(away_raw)

    # Open play: attack vs opposing defence, scaled by form + fatigue.
    home_open = LEAGUE_BASE_XG * home["attack"] * away["defence"] \
        * (0.85 + 0.30 * home["form"]) * home["fatigue_mult"]
    away_open = LEAGUE_BASE_XG * away["attack"] * home["defence"] \
        * (0.85 + 0.30 * away["form"]) * away["fatigue_mult"]

    # Set pieces contribute on top of open play (15-30% of real goals).
    home_xg = home_open + home["set_piece_threat"]
    away_xg = away_open + away["set_piece_threat"]

    return round(min(home_xg, 4.0), 3), round(min(away_xg, 4.0), 3)
