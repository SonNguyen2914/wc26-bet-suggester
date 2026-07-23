"""Model-development ladder + honest evaluation (V8.1 eval Phase 6).

The V8 evaluation raised two specific problems with the ad-hoc backtest:
  1. the model and its baseline used DIFFERENT simulation seeds, so
     their difference included independent Monte Carlo noise —
     "simulation noise masquerading as model improvement";
  2. the +0.007 log-loss edge had NO uncertainty estimate.

Both are fixed here. The 3-way outcome is scored with ANALYTIC
probabilities (independent-Poisson goal grid → exact P(home/draw/away)),
so there is zero simulation noise and every variant is compared on
identical ground. Uncertainty is a MATCH-CLUSTER bootstrap: resample
fixtures with replacement, recompute each variant's mean and every
pairwise edge, and report a 95% interval — so "M2 beats M0" is a claim
with a confidence interval, not a point estimate.

The ladder (evaluable rungs; M3+ await the inputs they need):
  M0  league scoring + home/away venue split (no team info)
  M1  team attack/defence ratings, equal-weighted, minimal pooling
  M2  + recency weighting + partial pooling  == mls-2026-v0
  M3  + rest / travel / surface           (pending covariates)
  M4  + availability / lineup effects      (data captured, not yet used)
  M5  + goalkeeper effects                 (data captured, not yet used)

Rolling-origin throughout: a fixture is predicted only from fixtures
that kicked off before it — no leakage by construction.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from src.live.db import get_session, plane_ready
from src.live.model_mls import HALF_LIFE_DAYS, MIN_GAMES, MODEL_NAME

EVAL_VERSION = "model-eval-v1"
GOAL_GRID = 15
THREE = ("home_win", "draw", "away_win")

# the evaluable rungs: (use_ratings, recency, shrink)
LADDER = {
    "M0": {"use_ratings": False, "recency": False, "shrink": 0.0,
           "desc": "league scoring + venue split"},
    "M1": {"use_ratings": True, "recency": False, "shrink": 1.0,
           "desc": "team ratings, equal-weighted, minimal pooling"},
    "M2": {"use_ratings": True, "recency": True, "shrink": 24.0,
           "desc": "+ recency + partial pooling (mls-2026-v0)"},
}
FUTURE_RUNGS = {
    "M3": "rest / travel / surface — pending covariates",
    "M4": "availability / lineup effects — data captured (Phase 5), "
          "not yet consumed pending this evaluation",
    "M5": "goalkeeper effects — data captured, not yet consumed",
}


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pois_pmf(lam: float) -> np.ndarray:
    lam = max(lam, 1e-6)
    k = np.arange(GOAL_GRID + 1)
    logf = np.array([math.lgamma(i + 1) for i in k])
    return np.exp(-lam + k * math.log(lam) - logf)


def analytic_3way(lam_h: float, lam_a: float) -> dict:
    """Exact P(home/draw/away) from independent Poisson goal counts —
    no simulation, hence no Monte Carlo noise."""
    ph, pa = _pois_pmf(lam_h), _pois_pmf(lam_a)
    joint = np.outer(ph, pa)                 # joint[h, a]
    home = float(np.tril(joint, -1).sum())   # h > a
    draw = float(np.trace(joint))            # h == a
    away = float(np.triu(joint, 1).sum())    # h < a
    tot = home + draw + away
    return {"home_win": home / tot, "draw": draw / tot,
            "away_win": away / tot}


def fit_variant(fixtures, as_of, cfg: dict) -> dict | None:
    """Ratings + league params under a ladder config. Pure function of
    its inputs; the walk-forward calls it with prior-only slices."""
    if not fixtures:
        return None
    gf, ga, wsum, games = {}, {}, {}, {}
    tot_home = tot_away = tot_w = 0.0
    for f in fixtures:
        if cfg["recency"]:
            days = (as_of - _utc(f.current_kickoff_utc)).total_seconds() / 86400
            w = 0.5 ** (max(days, 0.0) / HALF_LIFE_DAYS)
        else:
            w = 1.0
        for team, sc, co in ((f.home_team_id, f.home_goals, f.away_goals),
                             (f.away_team_id, f.away_goals, f.home_goals)):
            gf[team] = gf.get(team, 0.0) + w * sc
            ga[team] = ga.get(team, 0.0) + w * co
            wsum[team] = wsum.get(team, 0.0) + w
            games[team] = games.get(team, 0) + 1
        tot_home += w * f.home_goals
        tot_away += w * f.away_goals
        tot_w += w
    if tot_w <= 0:
        return None
    league = (tot_home + tot_away) / (2 * tot_w)
    if league <= 0:
        return None
    ratings = {}
    if cfg["use_ratings"]:
        k = cfg["shrink"]
        for team, w in wsum.items():
            ratings[team] = {
                "attack": (gf[team] / league + k) / (w + k),
                "defence": (ga[team] / league + k) / (w + k),
                "games": games[team]}
    return {"league": league,
            "venue_home": (tot_home / tot_w) / league,
            "venue_away": (tot_away / tot_w) / league,
            "ratings": ratings, "use_ratings": cfg["use_ratings"]}


def predict_variant(model: dict, fixture) -> dict | None:
    """Analytic 3-way for one fixture under a fitted variant."""
    lh_v, la_v = model["venue_home"], model["venue_away"]
    if model["use_ratings"]:
        h = model["ratings"].get(fixture.home_team_id)
        a = model["ratings"].get(fixture.away_team_id)
        if h is None or a is None or h["games"] < MIN_GAMES \
                or a["games"] < MIN_GAMES:
            return None
        lam_h = model["league"] * h["attack"] * a["defence"] * lh_v
        lam_a = model["league"] * a["attack"] * h["defence"] * la_v
    else:
        # M0 still needs enough history to be a fair comparison point
        lam_h = model["league"] * lh_v
        lam_a = model["league"] * la_v
    return analytic_3way(lam_h, lam_a)


def _rps(p: dict, result: str) -> float:
    """Ranked probability score for the ordered outcome home>draw>away."""
    order = ["home_win", "draw", "away_win"]
    cp = co = 0.0
    s = 0.0
    for k in order[:-1]:
        cp += p[k]
        co += 1.0 if k == result else 0.0
        s += (cp - co) ** 2
    return s / (len(order) - 1)


def _score_fixture(p: dict, result: str) -> dict:
    q = max(min(p[result], 1 - 1e-9), 1e-9)
    return {
        "log_loss": -math.log(q),
        "brier": sum((p[k] - (1.0 if k == result else 0.0)) ** 2
                     for k in THREE),
        "rps": _rps(p, result),
    }


def evaluate_ladder(n_boot: int = 1000, seed: int = 12345) -> dict:
    """Rolling-origin evaluation of every evaluable rung with analytic
    scoring and match-cluster bootstrap CIs on the pairwise edges."""
    if not plane_ready():
        return {"error": "dormant"}
    from src.live.model_mls import _completed
    s = get_session()
    try:
        rows = _completed(s)
    finally:
        s.close()
    # per-fixture per-variant scores (only fixtures every variant can
    # predict, so the comparison is on identical ground)
    per_fixture: list[dict] = []
    for i, f in enumerate(rows):
        prior, as_of = rows[:i], _utc(f.current_kickoff_utc)
        preds = {}
        ok = True
        for name, cfg in LADDER.items():
            m = fit_variant(prior, as_of, cfg)
            p = predict_variant(m, f) if m else None
            if p is None:
                ok = False
                break
            preds[name] = p
        if not ok:
            continue
        result = ("home_win" if f.home_goals > f.away_goals else
                  "away_win" if f.away_goals > f.home_goals else "draw")
        per_fixture.append({name: _score_fixture(preds[name], result)
                            for name in LADDER})
    n = len(per_fixture)
    if n == 0:
        return {"n_scored": 0, "note": "no fixtures scorable by all rungs"}

    def mean(name, metric, idx):
        return float(np.mean([per_fixture[j][name][metric] for j in idx]))

    full = list(range(n))
    variants = {
        name: {"log_loss": round(mean(name, "log_loss", full), 4),
               "brier": round(mean(name, "brier", full), 4),
               "rps": round(mean(name, "rps", full), 4),
               "desc": LADDER[name]["desc"]}
        for name in LADDER}

    # match-cluster bootstrap: resample fixtures with replacement
    rng = np.random.default_rng(seed)
    pairs = [("M2", "M0"), ("M2", "M1"), ("M1", "M0")]
    boot = {f"{a}_vs_{b}": [] for a, b in pairs}
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        for a, b in pairs:
            # positive edge = a has LOWER log loss than b (a is better)
            edge = mean(b, "log_loss", idx) - mean(a, "log_loss", idx)
            boot[f"{a}_vs_{b}"].append(edge)
    edges = {}
    for a, b in pairs:
        arr = np.array(boot[f"{a}_vs_{b}"])
        lo, hi = np.percentile(arr, [2.5, 97.5])
        point = mean(b, "log_loss", full) - mean(a, "log_loss", full)
        edges[f"{a}_vs_{b}"] = {
            "delta_log_loss": round(point, 4),
            "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "significant": bool(lo > 0 or hi < 0),
        }
    return {
        "eval_version": EVAL_VERSION,
        "method": ("analytic independent-Poisson 3-way, rolling-origin, "
                   "match-cluster bootstrap (no Monte Carlo noise)"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_scored": n, "n_bootstrap": n_boot,
        "variants": variants, "edges": edges,
        "future_rungs": FUTURE_RUNGS,
    }


def approval_record(report: dict, corpus_version: str | None = None) -> dict:
    """The model-approval decision record (V8.1 eval Phase 6). Shadow
    approval means 'safe to collect prospective evidence', explicitly
    NOT 'edge established' — and this record never grants a higher mode."""
    m2 = (report.get("variants") or {}).get("M2", {})
    e = (report.get("edges") or {}).get("M2_vs_M0", {})
    limitations = [
        "in-sample rolling-origin (not a prospective holdout)",
        "n and CI must be read together — a small point estimate with a "
        "CI spanning 0 is NOT an established edge",
        "M3-M5 rungs (rest/travel/lineup/GK) not yet implemented",
        "forecast quality only — market-relative and execution "
        "performance evaluated separately, after settlement",
    ]
    return {
        "model_version": MODEL_NAME,
        "corpus_version": corpus_version,
        "eval_version": report.get("eval_version"),
        "metrics": {"log_loss": m2.get("log_loss"),
                    "brier": m2.get("brier"), "rps": m2.get("rps"),
                    "n_scored": report.get("n_scored")},
        "edge_vs_baseline": e,
        "limitations": limitations,
        "approved_mode": "shadow",
        "approval_meaning": ("safe to collect prospective evidence; "
                             "NOT an established executable edge"),
        "approved_by": "automated-eval",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
