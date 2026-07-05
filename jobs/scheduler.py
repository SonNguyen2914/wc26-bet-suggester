"""Background jobs.

  poll_odds          : every ODDS_POLL_SECONDS (default 30s), record an
                       OddsReading for every open market on matches within
                       the prediction window — this is the learning corpus —
                       then score every watched market and fire a ripeness
                       alert the moment one crosses the threshold.
  hourly_predictions : every hour, re-simulate every match kicking off within
                       the configured window and refresh suggestions.
  final_lock_check   : every minute, lock a FINAL decision exactly once when a
                       match is <= 10 minutes from kickoff.
"""
from __future__ import annotations

from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

import config
from src.alerts import alert_final_lock, alert_new_take, alert_ripe
from src.db import SessionLocal, WatchlistItem, utcnow
from src.kalshi_client import KalshiClient
from src.schedule_data import load_schedule
from src.suggester import SuggesterEngine
from src.timing import compute_timing, record_reading, save_alert, should_alert

engine = SuggesterEngine()
kalshi = KalshiClient()
_finalized: set[str] = set()          # match_ids already locked this process
_model_probs: dict[str, float] = {}   # market_id -> latest model probability


def _refresh_model_cache(result: dict) -> None:
    """Keep the latest model probability per market so the 30s poller can
    compute edge without re-simulating (model refreshes hourly, odds don't
    wait)."""
    for s in result["suggestions"]:
        _model_probs[s["market_id"]] = s["model_probability"]


def hourly_predictions() -> None:
    now = utcnow()
    window = timedelta(hours=config.HOURLY_PREDICTION_WINDOW_HOURS)
    for match in load_schedule():
        if not (now < match.kickoff <= now + window):
            continue
        result = engine.run_for_match(match, source="scheduled")
        _refresh_model_cache(result)
        takes = [s for s in result["suggestions"] if s["recommendation"] == "TAKE"]
        print(f"[hourly] {match.match_id}: {len(takes)} TAKE / "
              f"{len(result['suggestions'])} markets")
        for s in takes[:3]:  # don't spam Discord
            alert_new_take(f"{match.home} vs {match.away}",
                           s["market_title"], s["edge"], s["expected_value"])


def poll_odds() -> None:
    """The always-on heartbeat: record every market's price, then check
    whether any watched bet just became ripe."""
    now = utcnow()
    window = timedelta(hours=config.HOURLY_PREDICTION_WINDOW_HOURS)
    matches = [m for m in load_schedule() if now < m.kickoff <= now + window]
    if not matches:
        return

    with SessionLocal() as session:
        watched = {w.market_id: w for w in
                   session.execute(select(WatchlistItem)).scalars().all()}

    for match in matches:
        for mkt in kalshi.get_markets_for_match(match):
            record_reading(match.match_id, mkt, _model_probs.get(mkt["market_id"]))

            item = watched.get(mkt["market_id"])
            if not item:
                continue
            timing = compute_timing(mkt["market_id"], match.kickoff)
            if should_alert(mkt["market_id"], timing):
                save_alert(match.match_id, mkt["market_id"], mkt["title"], timing)
                alert_ripe(f"{match.home} vs {match.away}", mkt["title"], timing)
                print(f"[RIPE {timing['score']:.0f}] {mkt['title']}")


def final_lock_check() -> None:
    now = utcnow()
    lock_delta = timedelta(minutes=config.FINAL_LOCK_MINUTES_BEFORE_KICKOFF)
    for match in load_schedule():
        if match.match_id in _finalized:
            continue
        time_left = match.kickoff - now
        if timedelta(0) < time_left <= lock_delta:
            result = engine.run_for_match(match, source="final_lock", is_final=True)
            _refresh_model_cache(result)
            _finalized.add(match.match_id)
            takes = [s for s in result["suggestions"] if s["recommendation"] == "TAKE"]
            best = takes[0] if takes else None
            alert_final_lock(f"{match.home} vs {match.away}", best)
            print(f"[FINAL LOCK] {match.match_id} locked at T-{time_left}")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(hourly_predictions, "cron", minute=0, id="hourly")
    scheduler.add_job(final_lock_check, "cron", second=0, id="final_lock")
    scheduler.add_job(poll_odds, "interval",
                      seconds=config.ODDS_POLL_SECONDS, id="odds_poll")
    scheduler.start()
    # Prime the cache immediately on boot so the dashboard isn't empty
    # and the poller has model probabilities to compute edge with.
    scheduler.add_job(hourly_predictions, "date", id="prime_predictions")
    scheduler.add_job(poll_odds, "date", id="prime_poll")
    return scheduler
