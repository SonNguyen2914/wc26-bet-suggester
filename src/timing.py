"""Bet-timing ("ripeness") engine.

Every ~30s the poller stores an OddsReading per market. This module turns
that growing history into a 0-100 "bet now" score:

  35%  edge z-score      how exceptional is the current edge vs today's
                         mean/std for this market? (3σ above → full marks)
  25%  price percentile  where do current decimal odds sit vs everything
                         we've seen for this market? (best price → 1.0)
  20%  momentum          is the edge growing or shrinking right now?
                         (slope over the last ~10 readings)
  10%  urgency           time-to-kickoff pressure (windows close)
  10%  liquidity         can you actually get the bet down?

The score is *self-improving without any model*: every reading tightens the
mean/std/percentile baselines, so alerts get sharper hour by hour and match
by match. Once ~10 matches of history exist, plug a Prophet/ARIMA forecast
into `forecast_component` (stub below) — the score interface won't change.

An alert only fires when score >= threshold AND the edge itself is positive
above the configured minimum — ripeness is about *when*, edge is about *if*.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from statistics import mean, pstdev

from sqlalchemy import select

import config
from src.db import OddsReading, SessionLocal, TimingAlert, utcnow

WEIGHTS = {"z": 0.35, "percentile": 0.25, "momentum": 0.20,
           "urgency": 0.10, "liquidity": 0.10}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def record_reading(match_id: str, market: dict, model_probability: float | None) -> None:
    edge = (model_probability - market["yes_price"]) if model_probability is not None else None
    with SessionLocal() as session:
        session.add(OddsReading(
            match_id=match_id,
            market_id=market["market_id"],
            yes_price=market["yes_price"],
            decimal_odds=market["decimal_odds"],
            model_probability=model_probability,
            edge=edge,
            volume_24h=market["volume_24h"],
        ))
        session.commit()


def compute_timing(market_id: str, kickoff, lookback_hours: int = 24) -> dict:
    """Score how ripe a market is right now, with a component breakdown."""
    since = utcnow() - timedelta(hours=lookback_hours)
    with SessionLocal() as session:
        readings = session.execute(
            select(OddsReading)
            .where(OddsReading.market_id == market_id,
                   OddsReading.created_at >= since)
            .order_by(OddsReading.created_at.asc())
        ).scalars().all()

    n = len(readings)
    if n == 0:
        return {"market_id": market_id, "score": 0, "status": "no_data",
                "readings": 0, "components": {}, "reasons": ["No readings yet"]}

    current = readings[-1]
    edges = [r.edge for r in readings if r.edge is not None]
    odds_hist = [r.decimal_odds for r in readings]

    provisional = n < config.RIPENESS_MIN_READINGS

    # --- edge z-score -------------------------------------------------
    if len(edges) >= 3 and current.edge is not None:
        mu, sigma = mean(edges), max(pstdev(edges), 1e-4)
        z = (current.edge - mu) / sigma
        z_comp = _clamp(z / 3.0)          # +3σ → 1.0, at/below mean → 0
    else:
        z, z_comp = 0.0, 0.0

    # --- price percentile ----------------------------------------------
    # For a YES buyer, higher decimal odds = cheaper contract = better price.
    pct = sum(1 for o in odds_hist if o <= current.decimal_odds) / n
    pct_comp = pct

    # --- momentum (edge slope over last ~10 readings) -------------------
    recent = edges[-10:]
    if len(recent) >= 2:
        slope = recent[-1] - recent[0]     # edge change across the window
        momentum_comp = _clamp(0.5 + slope * 25)   # ±2% swing → 0..1
    else:
        slope, momentum_comp = 0.0, 0.5

    # --- urgency ---------------------------------------------------------
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    hours_left = max((kickoff - utcnow()).total_seconds() / 3600.0, 0.0)
    urgency_comp = _clamp(1.0 - hours_left / 12.0)  # ramps over final 12h

    # --- liquidity ---------------------------------------------------------
    liq_comp = _clamp((current.volume_24h or 0) / config.MIN_VOLUME_24H)

    components = {"z": z_comp, "percentile": pct_comp, "momentum": momentum_comp,
                  "urgency": urgency_comp, "liquidity": liq_comp}
    score = 100 * sum(WEIGHTS[k] * v for k, v in components.items())
    if provisional:
        score *= 0.7  # damp until we have a real baseline

    reasons = [
        f"Edge {current.edge:+.1%} is {z:+.1f}σ vs today's average" if current.edge is not None else "No model edge yet",
        f"Odds {current.decimal_odds:.2f} beat {pct:.0%} of readings ({n} samples)",
        f"Edge momentum {slope:+.2%} over last {len(recent)} readings",
        f"{hours_left:.1f}h to kickoff",
        f"${current.volume_24h:,.0f} 24h volume",
    ]
    if provisional:
        reasons.append(f"Provisional: only {n}/{config.RIPENESS_MIN_READINGS} readings — score damped 30%")

    return {
        "market_id": market_id,
        "score": round(score, 1),
        "status": "provisional" if provisional else "learned",
        "readings": n,
        "current_edge": current.edge,
        "current_odds": current.decimal_odds,
        "components": {k: round(v, 3) for k, v in components.items()},
        "reasons": reasons,
    }


def should_alert(market_id: str, timing: dict) -> bool:
    """Fire only on genuinely ripe, positive-edge moments, with a cooldown."""
    if timing["score"] < config.RIPENESS_ALERT_THRESHOLD:
        return False
    if timing.get("current_edge") is None or timing["current_edge"] < config.MIN_EDGE:
        return False
    cutoff = utcnow() - timedelta(minutes=config.ALERT_COOLDOWN_MINUTES)
    with SessionLocal() as session:
        recent = session.execute(
            select(TimingAlert)
            .where(TimingAlert.market_id == market_id,
                   TimingAlert.created_at >= cutoff)
            .limit(1)
        ).scalar_one_or_none()
    return recent is None


def save_alert(match_id: str, market_id: str, market_title: str, timing: dict) -> None:
    with SessionLocal() as session:
        session.add(TimingAlert(
            match_id=match_id, market_id=market_id, market_title=market_title,
            score=timing["score"], decimal_odds=timing["current_odds"],
            edge=timing["current_edge"], reasons=" · ".join(timing["reasons"]),
        ))
        session.commit()


# ---------------------------------------------------------------------------
def forecast_component(market_id: str) -> float | None:
    """Phase-2 hook: once ~10 matches of odds_readings exist, train a
    Prophet/ARIMA model per market family here and return a 0-1 component
    ('current price is near the predicted local optimum'). Add it to WEIGHTS
    and renormalize — nothing else needs to change."""
    return None
