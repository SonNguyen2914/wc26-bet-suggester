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
from src.alerts import alert_final_lock, alert_new_take, alert_ripe, send_discord
from src.bracket import resolve_bracket
from src.db import SessionLocal, WatchlistItem, utcnow
from src.kalshi_client import KalshiClient
from src.live_signals import evaluate_live_signals
from src.model_cache import get_model_prob, refresh_model_cache
from src.schedule_data import is_trackable, load_schedule
from src import spike_detector
from src import live_state
from src.suggester import SuggesterEngine
from src.timing import compute_timing, record_reading, save_alert, should_alert

engine = SuggesterEngine()
kalshi = KalshiClient()
_finalized: set[str] = set()          # match_ids already locked this process


def hourly_predictions() -> None:
    now = utcnow()
    for match in load_schedule():
        if not is_trackable(match, now, config.HOURLY_PREDICTION_WINDOW_HOURS,
                            config.TRACK_HOURS_AFTER_KICKOFF):
            continue
        try:
            result = engine.run_for_match(match, source="scheduled")
        except Exception as exc:  # one bad match must never kill the batch
            print(f"[hourly] {match.match_id} FAILED: {exc}")
            continue
        refresh_model_cache(result)
        takes = [s for s in result["suggestions"] if s["recommendation"] == "TAKE"]
        print(f"[hourly] {match.match_id}: {len(takes)} TAKE / "
              f"{len(result['suggestions'])} markets")
        for s in takes[:3]:  # don't spam Discord
            alert_new_take(f"{match.home} vs {match.away}",
                           s["market_title"], s["edge"], s["expected_value"])


def live_tick() -> None:
    """Refresh live match-state snapshots and freeze finished matches.

    Its OWN fast job, decoupled from poll_odds: that job spends minutes on
    per-event Kalshi fetches, and while it runs APScheduler skips further
    fires (max_instances=1) — riding inside it degraded the live scoreboard
    to one update per ~2 minutes. This tick is one cached feed pull + a
    snapshot upsert, so it comfortably runs every LIVE_TICK_SECONDS."""
    try:
        r = live_state.poll_live_state()
        if r["frozen"]:
            print(f"[live-state] froze {r['frozen']} finished match(es)")
    except Exception as exc:
        print(f"[live-state] poll error: {exc}")


def poll_odds() -> None:
    """The always-on heartbeat: record every market's price, then check
    whether any watched bet just became ripe."""
    now = utcnow()
    matches = [m for m in load_schedule()
               if is_trackable(m, now, config.HOURLY_PREDICTION_WINDOW_HOURS,
                               config.TRACK_HOURS_AFTER_KICKOFF)]
    if not matches:
        return

    with SessionLocal() as session:
        watched = {w.market_id: w for w in
                   session.execute(select(WatchlistItem)).scalars().all()}

    for match in matches:
        try:
            mkts = kalshi.get_markets_for_match(match)
            # Layer 1 (LOG-ONLY): infer goals from the scoreline
            # distribution. Wrapped separately so a detector hiccup can
            # never disturb polling, and it touches nothing downstream.
            try:
                spike_detector.inspect(match.match_id, mkts)
            except Exception as exc:
                print(f"[spike] {match.match_id} detector error: {exc}")

            for mkt in mkts:
                record_reading(match.match_id, mkt,
                               get_model_prob(mkt["market_id"]))

                item = watched.get(mkt["market_id"])
                if not item:
                    continue
                timing = compute_timing(mkt["market_id"], match.kickoff)
                if should_alert(mkt["market_id"], timing):
                    save_alert(match.match_id, mkt["market_id"], mkt["title"],
                               timing)
                    alert_ripe(f"{match.home} vs {match.away}", mkt["title"],
                               timing)
                    print(f"[RIPE {timing['score']:.0f}] {mkt['title']}")
        except Exception as exc:  # keep polling the other matches
            print(f"[poll] {match.match_id} FAILED: {exc}")
            continue


def final_lock_check() -> None:
    now = utcnow()
    lock_delta = timedelta(minutes=config.FINAL_LOCK_MINUTES_BEFORE_KICKOFF)
    for match in load_schedule():
        if match.match_id in _finalized:
            continue
        time_left = match.kickoff - now
        if timedelta(0) < time_left <= lock_delta:
            try:
                result = engine.run_for_match(match, source="final_lock",
                                              is_final=True)
            except Exception as exc:  # retry on the next minute tick
                print(f"[final_lock] {match.match_id} FAILED: {exc}")
                continue
            refresh_model_cache(result)
            _finalized.add(match.match_id)
            takes = [s for s in result["suggestions"] if s["recommendation"] == "TAKE"]
            best = takes[0] if takes else None
            alert_final_lock(f"{match.home} vs {match.away}", best)
            print(f"[FINAL LOCK] {match.match_id} locked at T-{time_left}")


def live_signals_job() -> None:
    """BUY/SELL reads on WATCHED markets during live play. Piggybacks on the
    same ~25s-cached live_auto cycle the frontend stream reads, so a pass is
    nearly free; the module itself handles thresholds, cooldowns and pushes."""
    try:
        r = evaluate_live_signals(engine)
        if r["fired"]:
            print(f"[live-signals] fired {r['fired']} "
                  f"(checked {r['checked']} watched markets)")
    except Exception as exc:
        print(f"[live-signals] pass error: {exc}")


def bots_job() -> None:
    """The strategy-lab bots' pass: entries, exits, settlements. Rides the
    same cached prediction/live cycles everything else reads."""
    try:
        from src.bots import bots_tick
        r = bots_tick(engine)
        if r["opened"] or r["closed"] or r["settled"]:
            print(f"[bots] tick: {r['opened']} opened, {r['closed']} closed, "
                  f"{r['settled']} settled")
    except Exception as exc:
        print(f"[bots] tick error: {exc}")


def resolve_bracket_job() -> None:
    """Fill QF placeholder slots as R16 results land (fixtures only; team
    stats stay hand-sourced). Cheap and idempotent: does nothing once the
    bracket is fully known, so this can run often without burning feed budget.
    Announces each newly-decided matchup to Discord once."""
    try:
        changed = resolve_bracket()
    except Exception as exc:  # never let bracket work disturb the scheduler
        print(f"[bracket] resolve FAILED: {exc}")
        return
    for c in changed:
        send_discord(
            f"🗓️ Quarter-final set: **{c['team']}** advances into "
            f"{c['qf']} ({c['side']}).")


def boot_sequence() -> None:
    """Ordered boot recovery + prime — ONE job, because these raced as four
    independent one-shots. If the prediction prime ran before
    restore_missing_results had re-frozen finished results (the DB is wiped
    on every deploy) and the bracket resolver had filled the next round's
    slots, unresolved matches were skipped by the prime and the board sat on
    placeholder default-stats numbers until the next hourly cron (observed
    on prod 2026-07-12: SF2 served xg 1.398/1.398, advance ~0.50).

    The order is load-bearing:
      1. restore results   — bracket resolution reads frozen MatchResults
                             (the feed fallback can't fetch finished 2026
                             fixtures on the free plan);
      2. resolve bracket   — priming needs real team names in the slots;
      3. prime predictions — the odds poller needs model probs for edge;
      4. prime odds poll.
    Steps are isolated: a failing restore (ESPN down) must not leave the
    bracket unresolved or the dashboard unprimed."""
    for name, step in (
        ("restore_results", live_state.restore_missing_results),
        ("resolve_bracket", resolve_bracket_job),
        ("prime_predictions", hourly_predictions),
        ("prime_poll", poll_odds),
    ):
        try:
            step()
        except Exception as exc:
            print(f"[boot] {name} FAILED: {exc}")


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(hourly_predictions, "cron", minute=0, id="hourly")
    scheduler.add_job(final_lock_check, "cron", second=0, id="final_lock")
    scheduler.add_job(poll_odds, "interval",
                      seconds=config.ODDS_POLL_SECONDS, id="odds_poll")
    # Live scoreboard freshness: a fast, cheap tick of its own. coalesce
    # collapses any missed fires into one; max_instances guards overlap.
    scheduler.add_job(live_tick, "interval",
                      seconds=config.LIVE_TICK_SECONDS, id="live_tick",
                      coalesce=True, max_instances=1)
    # Watched-market BUY/SELL signals: instant no-op when nothing is both
    # live and watched, otherwise rides the cached live-read cycle.
    scheduler.add_job(live_signals_job, "interval",
                      seconds=config.LIVE_SIGNAL_POLL_SECONDS,
                      id="live_signals", coalesce=True, max_instances=1)
    # Paper-trading bots: instant no-op with no trackable matches, cheap
    # otherwise (cached predictions + cached live cycle + signal rows).
    scheduler.add_job(bots_job, "interval", seconds=60,
                      id="bots", coalesce=True, max_instances=1)
    # Bracket resolution: low frequency (the bracket changes at most a handful
    # of times all tournament) and self-skipping once fully known, so it's
    # nearly free. boot_sequence covers the boot-time resolve.
    scheduler.add_job(resolve_bracket_job, "interval",
                      minutes=config.BRACKET_RESOLVE_MINUTES, id="bracket")
    scheduler.start()
    # One-shot at boot, ORDERED: restore wiped results -> resolve bracket
    # slots -> prime predictions -> prime the odds poll. A single chained
    # job — as independent one-shots these raced each other (see
    # boot_sequence docstring).
    scheduler.add_job(boot_sequence, "date", id="boot_sequence")
    return scheduler
