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


def latest_for_match(match_id: str, final_only: bool = False) -> dict | None:
    """Most recent prediction batch for a match, grouped by market.
    final_only=True restricts to the T-10 LOCKED batch — the model's
    committed pre-kickoff view, which is what a settled match's review
    page shows."""
    with SessionLocal() as session:
        where = [Prediction.match_id == match_id]
        if final_only:
            where.append(Prediction.is_final)
        newest = session.execute(
            select(Prediction)
            .where(*where)
            .order_by(Prediction.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not newest:
            return None

        # Pull a recent window and keep the LATEST row per market_id. Two runs
        # for the same match can overlap (a manual refresh-all landing near the
        # hourly/boot job); a naive time-window would then return every market
        # TWICE — once per run, with slightly different Monte Carlo probs —
        # which showed up as duplicated rows on the board. Deduping to the
        # freshest row per market_id fixes that and always shows current prices.
        # 60s comfortably covers a single batch's write span.
        from datetime import timedelta
        batch_time = newest.created_at
        recent = session.execute(
            select(Prediction)
            .where(*where,
                   Prediction.created_at >= batch_time - timedelta(seconds=60))
            .order_by(Prediction.created_at.desc())
        ).scalars().all()

    seen: set[str] = set()
    rows = []
    for r in recent:                 # newest-first, so the first per id wins
        if r.market_id in seen:
            continue
        seen.add(r.market_id)
        rows.append(r)
    rows.sort(key=lambda r: r.expected_value, reverse=True)

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
        "summary": json.loads(newest.summary_json) if newest.summary_json else None,
        "confidence": newest.confidence,
        "markets": [
            {
                "market_id": r.market_id,
                "market_title": r.market_title,
                "outcome_key": r.outcome_key,
                "model_probability": r.model_probability,
                "kalshi_odds": r.kalshi_odds,
                "implied_probability": r.implied_probability,
                "edge": r.edge,
                "expected_value": r.expected_value,
            }
            for r in rows
        ],
    }


def timeline_for_match(match_id: str, outcome_key: str = "home_win",
                       limit: int = 24) -> list[dict]:
    """How one outcome's prediction evolved across runs (timeline view).

    Filters by the classified outcome_key, which works for real Kalshi
    tickers. The old ticker-substring filter ("%HOME_WIN%") only ever
    matched demo-mode's synthetic tickers, so this endpoint silently
    returned nothing in live mode.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(Prediction)
            .where(Prediction.match_id == match_id,
                   Prediction.outcome_key == outcome_key)
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
