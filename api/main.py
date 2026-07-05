"""FastAPI app — the on-demand layer.

Endpoints
  GET  /api/health
  GET  /api/matches/upcoming?hours_ahead=48
  GET  /api/suggestions                      ranked TAKE bets across matches
  GET  /api/prediction/{match_id}            cached (or fresh if stale/missing)
  GET  /api/prediction/{match_id}?force_refresh=true
  GET  /api/prediction/{match_id}/timeline   how the prediction evolved
  POST /api/prediction/{match_id}/refresh    force a fresh run
  GET  /api/settings                         current thresholds
  POST /api/settings                         update thresholds
"""
from __future__ import annotations

import time
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

import config
from src.cache import latest_for_match, timeline_for_match
from src.db import (SessionLocal, Suggestion, get_setting, init_db,
                    set_setting, utcnow)
from src.schedule_data import get_match, load_schedule
from src.suggester import SuggesterEngine

app = FastAPI(title="Kalshi WC26 Bet Suggester", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = SuggesterEngine()


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "demo_mode": config.DEMO_MODE, "time": utcnow().isoformat()}


@app.get("/api/matches/upcoming")
def upcoming_matches(hours_ahead: int = Query(48, ge=1, le=720)):
    now = utcnow()
    horizon = now + timedelta(hours=hours_ahead)
    out = []
    for m in load_schedule():
        if not (now < m.kickoff <= horizon):
            continue
        cached = latest_for_match(m.match_id)
        out.append({
            "match_id": m.match_id,
            "home": m.home,
            "away": m.away,
            "group": m.group,
            "stage": m.stage,
            "venue": m.venue,
            "kickoff": m.kickoff.isoformat(),
            "seconds_to_kickoff": int((m.kickoff - now).total_seconds()),
            "has_prediction": cached is not None,
            "is_final": bool(cached and cached["is_final"]),
            "confidence": cached["confidence"] if cached else None,
        })
    out.sort(key=lambda x: x["seconds_to_kickoff"])
    return {"matches": out, "generated_at": now.isoformat()}


@app.get("/api/suggestions")
def suggestions(limit: int = Query(25, ge=1, le=100)):
    """Latest TAKE suggestions across all matches, ranked by EV."""
    with SessionLocal() as session:
        rows = session.execute(
            select(Suggestion)
            .where(Suggestion.recommendation == "TAKE",
                   Suggestion.kickoff > utcnow())
            .order_by(Suggestion.created_at.desc())
            .limit(300)
        ).scalars().all()

    # de-dupe: keep newest row per market
    seen, unique = set(), []
    for r in rows:
        if r.market_id in seen:
            continue
        seen.add(r.market_id)
        # Kelly fraction: the honest ranking — how much of a bankroll a
        # rational bettor would stake. Penalizes longshots that raw EV loves.
        b = max((r.kalshi_odds or 1.01) - 1, 0.01)
        kelly = max((r.model_probability * (b + 1) - 1) / b, 0.0)
        unique.append({
            "match_id": r.match_id,
            "market_id": r.market_id,
            "market_title": r.market_title,
            "kickoff": r.kickoff.isoformat() if r.kickoff else None,
            "kalshi_odds": r.kalshi_odds,
            "model_probability": r.model_probability,
            "implied_probability": r.implied_probability,
            "edge": r.edge,
            "expected_value": r.expected_value,
            "kelly_fraction": round(kelly, 4),
            "confidence": r.confidence,
            "is_final": r.is_final,
            "reason": r.reason,
        })
    # Rank by Kelly, then cap per match so one opinion can't flood the list
    unique.sort(key=lambda x: x["kelly_fraction"], reverse=True)
    per_match: dict[str, int] = {}
    capped = []
    for s in unique:
        n = per_match.get(s["match_id"], 0)
        if n >= config.MAX_SUGGESTIONS_PER_MATCH:
            continue
        per_match[s["match_id"]] = n + 1
        capped.append(s)
    return {"suggestions": capped[:limit], "generated_at": utcnow().isoformat()}


@app.get("/api/prediction/{match_id}")
def get_prediction(match_id: str, force_refresh: bool = False):
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")

    if not force_refresh:
        cached = latest_for_match(match_id)
        if cached and not cached["is_stale"]:
            return {"freshness": "cached", **cached}

    t0 = time.time()
    result = engine.run_for_match(match, source="on_demand")
    fresh = latest_for_match(match_id)
    return {
        "freshness": "fresh",
        "inference_time_ms": round((time.time() - t0) * 1000),
        "suggestions": result["suggestions"],
        **fresh,
    }


@app.get("/api/prediction/{match_id}/timeline")
def prediction_timeline(match_id: str, market_suffix: str = "HOME_WIN"):
    if not get_match(match_id):
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    points = timeline_for_match(match_id, market_suffix=market_suffix)
    return {"match_id": match_id, "market_suffix": market_suffix,
            "points": points, "count": len(points)}


@app.post("/api/prediction/{match_id}/refresh")
def refresh_prediction(match_id: str):
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    result = engine.run_for_match(match, source="on_demand")
    return {"status": "refreshed", "match_id": match_id,
            "suggestions": result["suggestions"],
            "generated_at": result["generated_at"]}


# ---------------------------------------------------------------------------
# Watchlist + ripeness timing
# ---------------------------------------------------------------------------
from src.db import TimingAlert, WatchlistItem
from src.timing import compute_timing


class WatchIn(BaseModel):
    match_id: str
    market_id: str
    market_title: str | None = None


@app.get("/api/watchlist")
def get_watchlist():
    """Watched markets, each with its live ripeness score."""
    with SessionLocal() as session:
        items = session.execute(select(WatchlistItem)).scalars().all()
    out = []
    for item in items:
        match = get_match(item.match_id)
        timing = (compute_timing(item.market_id, match.kickoff)
                  if match else {"score": 0, "status": "match_over",
                                 "readings": 0, "components": {}, "reasons": []})
        out.append({
            "match_id": item.match_id,
            "market_id": item.market_id,
            "market_title": item.market_title,
            "watched_since": item.created_at.isoformat(),
            "timing": timing,
        })
    out.sort(key=lambda x: x["timing"]["score"], reverse=True)
    return {"watchlist": out, "alert_threshold": config.RIPENESS_ALERT_THRESHOLD}


@app.post("/api/watchlist")
def add_watch(body: WatchIn):
    if not get_match(body.match_id):
        raise HTTPException(404, f"Unknown match_id '{body.match_id}'")
    with SessionLocal() as session:
        exists = session.execute(
            select(WatchlistItem).where(WatchlistItem.market_id == body.market_id)
        ).scalar_one_or_none()
        if exists:
            return {"status": "already_watching", "market_id": body.market_id}
        session.add(WatchlistItem(match_id=body.match_id, market_id=body.market_id,
                                  market_title=body.market_title))
        session.commit()
    return {"status": "watching", "market_id": body.market_id,
            "note": f"You'll be alerted when the ripeness score crosses "
                    f"{config.RIPENESS_ALERT_THRESHOLD:.0f} with positive edge."}


@app.delete("/api/watchlist/{market_id}")
def remove_watch(market_id: str):
    with SessionLocal() as session:
        item = session.execute(
            select(WatchlistItem).where(WatchlistItem.market_id == market_id)
        ).scalar_one_or_none()
        if not item:
            raise HTTPException(404, "Not on watchlist")
        session.delete(item)
        session.commit()
    return {"status": "removed", "market_id": market_id}


@app.get("/api/timing/{match_id}/{market_id}")
def get_timing(match_id: str, market_id: str):
    """Full ripeness breakdown for any market (watched or not)."""
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    return compute_timing(market_id, match.kickoff)


@app.get("/api/alerts/recent")
def recent_alerts(limit: int = Query(20, ge=1, le=100)):
    """Notification feed: every ripeness alert that has fired."""
    with SessionLocal() as session:
        rows = session.execute(
            select(TimingAlert).order_by(TimingAlert.created_at.desc()).limit(limit)
        ).scalars().all()
    return {"alerts": [
        {
            "match_id": r.match_id,
            "market_id": r.market_id,
            "market_title": r.market_title,
            "score": r.score,
            "decimal_odds": r.decimal_odds,
            "edge": r.edge,
            "reasons": r.reasons,
            "fired_at": r.created_at.isoformat(),
        } for r in rows
    ]}


# ---------------------------------------------------------------------------
class SettingsIn(BaseModel):
    min_edge: float | None = None
    min_confidence: float | None = None
    min_volume: float | None = None


@app.get("/api/settings")
def get_settings():
    with SessionLocal() as session:
        return {
            "min_edge": get_setting(session, "min_edge", config.MIN_EDGE),
            "min_confidence": get_setting(session, "min_confidence", config.MIN_CONFIDENCE),
            "min_volume": get_setting(session, "min_volume", config.MIN_VOLUME_24H),
        }


@app.post("/api/settings")
def update_settings(body: SettingsIn):
    with SessionLocal() as session:
        for key, value in body.model_dump(exclude_none=True).items():
            set_setting(session, key, value)
    return {"status": "saved", **get_settings()}
