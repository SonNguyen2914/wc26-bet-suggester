"""Feature engineering for the xG model.

Implements the reliability layers we scoped:
  - regression to the mean on recent form
  - fatigue penalty
  - tournament-stage adjustment
"""
from __future__ import annotations

import math


def regressed_form(form: float, n_recent: int = 5, stability: float = 3.0) -> float:
    """Pull hot/cold streaks back toward a neutral 0.5 baseline.

    weight = sqrt(n) / (sqrt(n) + k). With n=5, k=3 → ~43% weight on recent.
    """
    w = math.sqrt(n_recent) / (math.sqrt(n_recent) + stability)
    return w * form + (1 - w) * 0.5


def fatigue_multiplier(fatigue_score: float) -> float:
    """fatigue 0..1 → performance multiplier 1.0..0.85 (max 15% penalty)."""
    return 1.0 - 0.15 * min(max(fatigue_score, 0.0), 1.0)


def stage_uncertainty(stage: str) -> float:
    """Knockout football is higher-variance: widen the model's uncertainty."""
    return 1.10 if stage == "knockout" else 1.0


def build_team_features(raw: dict) -> dict:
    """Turn raw team stats into the inputs the xG model consumes."""
    return {
        "attack": raw["attack"],
        "defence": raw["defence"],
        "form": regressed_form(raw["form"]),
        "fatigue_mult": fatigue_multiplier(raw["fatigue"]),
        "set_piece_threat": raw["set_piece_threat"],
        "red_card_risk": raw["red_card_risk"],
        "elo": raw["elo"],
    }
