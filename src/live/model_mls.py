"""mls-2026-v0 — the first MLS model (launch decision O8).

An interpretable goals-rate baseline run through the SHARED Monte Carlo
engine, with every parameter fitted from MLS 2026 data held in the live
database — WC26 values are structure, never coefficients:

  - attack_i  = shrunk, recency-weighted (GF/game)_i relative to league
  - defence_i = shrunk, recency-weighted (GA/game)_i relative to league
  - league_base = the season's goals per team-game (fitted, not 1.30)
  - venue multipliers = fitted home/away scoring split (not WC-neutral)
  - set-piece term: NEUTRAL (threat == baseline -> centered adj == 0);
    per the decision, no set-piece adjustment without decomposed inputs
  - form neutral 0.5, fatigue 0 (no validated covariates yet — a zero
    is honest where data quality is weak)

Validation: rolling-origin walk-forward over the season's completed
fixtures (no future match ever informs an earlier rating), scored
against a league-average baseline. approved_for_shadow is EARNED by
beating that baseline, not assumed.
"""
from __future__ import annotations

import hashlib
import math
from datetime import timezone

from src.live.db import get_session, plane_ready
from src.live.models import Fixture, ModelVersion

MODEL_NAME = "mls-2026-v0"
# SHRINK_GAMES chosen by the rolling-origin sweep of Jul 23 (n=162
# fixtures, 4000 sims): k=6 LOST to the flat baseline (-0.007 logloss);
# k in [20,56] is uniformly positive with a stable optimum near 24
# (+0.007). MLS scoring rates are noisy enough that a season's raw
# GF/GA needs to be pulled hard toward the mean. The edge is real but
# SMALL — one more reason the money gate stays closed.
SHRINK_GAMES = 24.0         # Bayesian prior weight (games at league avg)
HALF_LIFE_DAYS = 90.0       # recency half-life for rate weighting
MIN_GAMES = 5               # a team needs history before it's rated


def _weight(days_ago: float) -> float:
    return 0.5 ** (max(days_ago, 0.0) / HALF_LIFE_DAYS)


def _completed(s, before=None):
    q = (s.query(Fixture)
         .filter_by(competition_slug="mls-2026", status="post")
         .filter(Fixture.home_goals.isnot(None),
                 Fixture.home_team_id.isnot(None),
                 Fixture.away_team_id.isnot(None)))
    rows = [f for f in q.all() if f.current_kickoff_utc is not None]
    if before is not None:
        rows = [f for f in rows if _utc(f.current_kickoff_utc) < before]
    rows.sort(key=lambda f: _utc(f.current_kickoff_utc))
    return rows


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fit(fixtures, as_of) -> dict | None:
    """Ratings + league parameters from a list of completed fixtures.
    Pure function of its inputs — the walk-forward validator calls it
    with prior-only slices."""
    if not fixtures:
        return None
    gf: dict[int, float] = {}
    ga: dict[int, float] = {}
    w_sum: dict[int, float] = {}
    n_games: dict[int, int] = {}
    tot_home = tot_away = tot_w = 0.0
    for f in fixtures:
        days = (as_of - _utc(f.current_kickoff_utc)).total_seconds() / 86400
        w = _weight(days)
        for team, scored, conceded in (
                (f.home_team_id, f.home_goals, f.away_goals),
                (f.away_team_id, f.away_goals, f.home_goals)):
            gf[team] = gf.get(team, 0.0) + w * scored
            ga[team] = ga.get(team, 0.0) + w * conceded
            w_sum[team] = w_sum.get(team, 0.0) + w
            n_games[team] = n_games.get(team, 0) + 1
        tot_home += w * f.home_goals
        tot_away += w * f.away_goals
        tot_w += w
    if tot_w <= 0:
        return None
    league_gpg = (tot_home + tot_away) / (2 * tot_w)
    if league_gpg <= 0:
        return None
    ratings = {}
    for team, w in w_sum.items():
        # shrink toward league average by prior weight (in games)
        k = SHRINK_GAMES
        atk = (gf[team] / league_gpg + k) / (w + k)
        dfc = (ga[team] / league_gpg + k) / (w + k)
        ratings[team] = {"attack": atk, "defence": dfc,
                         "games": n_games[team]}
    return {
        "league_gpg": league_gpg,
        # fitted venue split: home teams score tot_home/tot_w per game
        "venue_home": (tot_home / tot_w) / league_gpg,
        "venue_away": (tot_away / tot_w) / league_gpg,
        "ratings": ratings,
        "n_fixtures": len(fixtures),
    }


def _raw(team_id: int, model: dict, venue: str) -> dict | None:
    r = model["ratings"].get(team_id)
    if r is None or r["games"] < MIN_GAMES:
        return None
    from src.models.xg_model import SET_PIECE_BASELINE
    return {
        "attack": r["attack"], "defence": r["defence"],
        "form": 0.5, "fatigue": 0.0,
        "set_piece_threat": SET_PIECE_BASELINE,   # centered adj == 0
        "red_card_risk": 0.06,
        # engine pass-through; only elo DIFFERENCE is ever consumed
        # (WC26 anchoring), so equal values are a true neutral
        "elo": 1500.0,
        "league_base": model["league_gpg"],
        "venue_mult": model[f"venue_{venue}"],
    }


def seed_for(fixture_id: int, run_type: str) -> int:
    """Deterministic per-(fixture, run_type) seed, masked to 31 bits:
    prediction_run.simulation_seed is a SIGNED 32-bit integer on
    PostgreSQL, and an unmasked sha prefix >= 2^31 killed every boot
    run sweep on prod (Jul 23) while sqlite happily stored it."""
    h = hashlib.sha256(f"{MODEL_NAME}:{fixture_id}:{run_type}"
                      .encode()).hexdigest()
    return int(h[:8], 16) & 0x7FFFFFFF


def predict_fixture(fixture, model: dict, run_type: str = "scheduled",
                    n_sims: int | None = None) -> dict | None:
    """One fixture's shadow prediction via the shared engine."""
    home = _raw(fixture.home_team_id, model, "home")
    away = _raw(fixture.away_team_id, model, "away")
    if home is None or away is None:
        return None
    from src.models.simulator import MatchSimulator
    sim = MatchSimulator(n_simulations=n_sims,
                         seed=seed_for(fixture.id, run_type))
    out = sim.simulate(home, away, stage="group")
    return {
        "model_version": MODEL_NAME,
        "seed": seed_for(fixture.id, run_type),
        "outcomes": out["outcomes"],
        "props": {k: out["props"][k] for k in
                  ("btts", "over_1_5", "over_2_5", "over_3_5")
                  if k in out["props"]},
        "scorelines": out["scorelines"][:6],
        "xg": out["xg"],
        "basis": {
            "home_games": model["ratings"][fixture.home_team_id]["games"],
            "away_games": model["ratings"][fixture.away_team_id]["games"],
            "league_gpg": round(model["league_gpg"], 3),
            "venue_home": round(model["venue_home"], 3),
            # fitted ratings relative to league 1.0 — the honest "how
            # they play" numbers (attack >1 scores more than average,
            # defence <1 concedes less)
            "home_attack": round(model["ratings"][fixture.home_team_id]["attack"], 3),
            "home_defence": round(model["ratings"][fixture.home_team_id]["defence"], 3),
            "away_attack": round(model["ratings"][fixture.away_team_id]["attack"], 3),
            "away_defence": round(model["ratings"][fixture.away_team_id]["defence"], 3),
        },
    }


def current_model() -> dict | None:
    """Fit from everything completed as of now."""
    if not plane_ready():
        return None
    from datetime import datetime
    s = get_session()
    try:
        rows = _completed(s)
        return fit(rows, datetime.now(timezone.utc))
    finally:
        s.close()


# --- rolling-origin validation --------------------------------------------

def _logloss3(p: dict, result: str) -> float:
    q = max(min(p[result], 1 - 1e-6), 1e-6)
    return -math.log(q)


def backtest(n_sims: int = 4000) -> dict:
    """Walk-forward over the season: every completed fixture whose two
    teams each have >= MIN_GAMES PRIOR completed games is predicted from
    prior-only data and scored against the result. Baseline = the same
    machinery with flat ratings (league average + fitted venue split) —
    v0 must beat what 'every team is identical' already knows."""
    if not plane_ready():
        return {"error": "dormant"}
    s = get_session()
    try:
        rows = _completed(s)
    finally:
        s.close()
    scored = []
    from src.models.simulator import MatchSimulator
    for i, f in enumerate(rows):
        prior = rows[:i]
        as_of = _utc(f.current_kickoff_utc)
        model = fit(prior, as_of)
        if model is None:
            continue
        pred = predict_fixture(f, model, run_type="backtest",
                               n_sims=n_sims)
        if pred is None:
            continue
        flat = dict(model)
        flat["ratings"] = {t: {"attack": 1.0, "defence": 1.0,
                               "games": model["ratings"][t]["games"]}
                          for t in model["ratings"]}
        base = predict_fixture(f, flat, run_type="baseline", n_sims=n_sims)
        result = ("home_win" if f.home_goals > f.away_goals else
                  "away_win" if f.away_goals > f.home_goals else "draw")
        o, b = pred["outcomes"], base["outcomes"]
        scored.append({
            "fixture": f.espn_event_id,
            "result": result,
            "model_p": o[result],
            "ll_model": _logloss3(o, result),
            "ll_base": _logloss3(b, result),
            "brier_model": sum((o[k] - (1.0 if k == result else 0.0)) ** 2
                               for k in ("home_win", "draw", "away_win")),
            "brier_base": sum((b[k] - (1.0 if k == result else 0.0)) ** 2
                              for k in ("home_win", "draw", "away_win")),
            "picked": max(o, key=o.get) == result,
        })
    n = len(scored)
    if n == 0:
        return {"n": 0, "error": "no scorable fixtures"}
    ll_m = sum(r["ll_model"] for r in scored) / n
    ll_b = sum(r["ll_base"] for r in scored) / n
    return {
        "model_version": MODEL_NAME, "n": n,
        "logloss_model": round(ll_m, 4),
        "logloss_baseline": round(ll_b, 4),
        "logloss_edge": round(ll_b - ll_m, 4),      # positive = model wins
        "brier_model": round(sum(r["brier_model"] for r in scored) / n, 4),
        "brier_baseline": round(sum(r["brier_base"] for r in scored) / n, 4),
        "winner_hit_rate": round(sum(r["picked"] for r in scored) / n, 4),
        "beats_baseline": ll_m < ll_b,
    }


def ensure_model_version(approved_for_shadow: bool) -> None:
    """Upsert the model_version row with its earned approval flag.
    approved_for_real_money is NEVER set here — that flag belongs to the
    evidence-review gate alone."""
    if not plane_ready():
        return
    from datetime import datetime
    s = get_session()
    try:
        row = s.query(ModelVersion).filter_by(name=MODEL_NAME).first()
        if row is None:
            row = ModelVersion(name=MODEL_NAME,
                               description="goals-rate baseline, fitted "
                                           "MLS params, shared engine",
                               created_at=datetime.now(timezone.utc))
            s.add(row)
        row.approved_for_shadow = bool(approved_for_shadow)
        s.commit()
    finally:
        s.close()
