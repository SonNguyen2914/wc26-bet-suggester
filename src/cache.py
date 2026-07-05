"""Prediction cache backed by the predictions table.

The on-demand API checks here first; a hit under TTL returns instantly,
a stale hit returns with a warning, a miss (or force_refresh) triggers a
fresh simulation.
"""
from __future__ import annotations

import json
from datetime import timezone

from sqlalchemy import select

import config
from src.db import Prediction, SessionLocal, utcnow


def latest_for_match(match_id: str) -> dict | None:
    """Most recent prediction batch for a match, grouped by market."""
    with SessionLocal() as session:
        newest = session.execute(
            select(Prediction)
            .where(Prediction.match_id == match_id)
            .order_by(Prediction.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not newest:
            return None

        # Rows written in one batch get microsecond-different timestamps,
        # so group by a 3-second window around the newest row.
        from datetime import timedelta
        batch_time = newest.created_at
        rows = session.execute(
            select(Prediction)
            .where(Prediction.match_id == match_id,
                   Prediction.created_at >= batch_time - timedelta(seconds=3))
            .order_by(Prediction.expected_value.desc())
        ).scalars().all()

    if batch_time.tzinfo is None:  # SQLite drops tzinfo
        batch_time = batch_time.replace(tzinfo=timezone.utc)
    age = (utcnow() - batch_time).total_seconds()

    return {
        "match_id": match_id,
        "generated_at": batch_time.isoformat(),
        "age_seconds": int(age),
        "is_stale": age > config.PREDICTION_CACHE_TTL_SECONDS,
        "source": newest.source,
        "is_final": newest.is_final,
        "xg": {"home": newest.xg_home, "away": newest.xg_away},
        "scorelines": json.loads(newest.scoreline_json or "[]"),
        "confidence": newest.confidence,
        "markets": [
            {
                "market_id": r.market_id,
                "market_title": r.market_title,
                "model_probability": r.model_probability,
                "kalshi_odds": r.kalshi_odds,
                "implied_probability": r.implied_probability,
                "edge": r.edge,
                "expected_value": r.expected_value,
            }
            for r in rows
        ],
    }


def timeline_for_match(match_id: str, market_suffix: str = "HOME_WIN",
                       limit: int = 24) -> list[dict]:
    """How the headline prediction evolved across runs (for the timeline view)."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Prediction)
            .where(Prediction.match_id == match_id,
                   Prediction.market_id.like(f"%{market_suffix}%"))
            .order_by(Prediction.created_at.desc())
            .limit(limit)
        ).scalars().all()

    out = []
    for r in reversed(rows):
        ts = r.created_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append({
            "timestamp": ts.isoformat(),
            "model_probability": r.model_probability,
            "kalshi_odds": r.kalshi_odds,
            "implied_probability": r.implied_probability,
            "edge": r.edge,
            "confidence": r.confidence,
            "xg_home": r.xg_home,
            "xg_away": r.xg_away,
            "source": r.source,
            "is_final": r.is_final,
        })
    return out
