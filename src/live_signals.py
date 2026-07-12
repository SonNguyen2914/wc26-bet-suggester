"""In-play BUY/SELL signals on WATCHED markets.

"Watch" is Son's declaration: I'm betting (or holding) this market. Once
the match is live, the self-running live read re-prices every open book
from the remaining-match simulation; when that live model probability
diverges from the market's own price beyond LIVE_SIGNAL_MIN_DIFF, this
module fires a signal:

  BUY  — live model prices YES above the market (model − market >= +thr):
         the position looks cheap right now, add/enter.
  SELL — live model prices YES below the market (model − market <= −thr):
         the market is paying more than the model thinks it's worth,
         exit/take profit.

Anti-spam: a market re-fires only after LIVE_SIGNAL_COOLDOWN_SECONDS have
passed AND the read materially changed — the side flipped, or the
divergence strengthened by >= RESTRENGTHEN beyond the last fired value.

This is the one deliberate exception to the "live edge is informational
only" rule, and it stays narrow: only markets Son explicitly watched, never
a board-wide TAKE resurrection. Model-only rows (Kalshi book closed) can't
fire — there is nothing to buy or sell.
"""
from __future__ import annotations

import time

from sqlalchemy import select

import config
from src.alerts import send_discord
from src.cache import latest_for_match
from src.db import LiveSignal, MatchLiveSnapshot, SessionLocal, WatchlistItem
from src.live_auto import live_auto
from src.schedule_data import load_schedule

# A repeat signal on the same side needs the divergence to have grown by at
# least this much past the previously fired value ("it got MORE mispriced").
RESTRENGTHEN = 0.05

# market_id -> {"side": str, "diff": float, "ts": float} — last FIRED signal.
# In-memory on purpose: after a restart the worst case is one repeated
# signal per watched market, which is honest anyway (the read still holds).
_state: dict[str, dict] = {}


def _decide(row: dict) -> tuple[str, float] | None:
    """BUY/SELL side for one priced market row, or None inside the band."""
    model_p = row.get("live_model_probability")
    market_p = row.get("market_probability")
    if model_p is None or market_p is None:      # model-only row: no book
        return None
    diff = model_p - market_p
    # tiny epsilon so an exactly-at-threshold gap fires despite float noise
    # (prices are whole cents: 0.62 - 0.54 must count as 0.08, not 0.0799…)
    thr = config.LIVE_SIGNAL_MIN_DIFF - 1e-9
    if diff >= thr:
        return "BUY", diff
    if diff <= -thr:
        return "SELL", diff
    return None


def _should_fire(market_id: str, side: str, diff: float,
                 now_ts: float | None = None) -> bool:
    prev = _state.get(market_id)
    if prev is None:
        return True
    now_ts = time.time() if now_ts is None else now_ts
    if now_ts - prev["ts"] < config.LIVE_SIGNAL_COOLDOWN_SECONDS:
        return False
    if side != prev["side"]:
        return True
    return abs(diff) - abs(prev["diff"]) >= RESTRENGTHEN


def _mark_fired(market_id: str, side: str, diff: float,
                now_ts: float | None = None) -> None:
    _state[market_id] = {"side": side, "diff": diff,
                         "ts": time.time() if now_ts is None else now_ts}


def evaluate_live_signals(engine) -> dict:
    """One evaluation pass: every LIVE match that has watched markets gets
    its (cached) live-read cycle, each watched market is checked against
    the threshold, and new signals persist + push. Cheap by construction —
    live_auto is the same ~25s-cached cycle the frontend stream reads, so
    this piggybacks rather than re-simulating."""
    checked = fired = 0
    with SessionLocal() as s:
        items = s.execute(select(WatchlistItem)).scalars().all()
        watched_by_match: dict[str, list] = {}
        for w in items:
            watched_by_match.setdefault(w.match_id, []).append(
                {"market_id": w.market_id, "market_title": w.market_title})
        live_ids = set(s.execute(select(MatchLiveSnapshot.match_id)).scalars())

    active = {mid: w for mid, w in watched_by_match.items() if mid in live_ids}
    if not active:
        return {"checked": 0, "fired": 0}

    for match in load_schedule():
        watched = active.get(match.match_id)
        if not watched:
            continue
        try:
            out = live_auto(match, engine,
                            (latest_for_match(match.match_id) or {}).get("xg"))
        except Exception as exc:   # a feed hiccup must never kill the job
            print(f"[live-signals] {match.match_id} cycle failed: {exc}")
            continue
        if not out.get("available"):
            continue
        rows = {r["market_id"]: r for r in out.get("markets", [])}
        minute = (out.get("live_state") or {}).get("minutes_elapsed")

        for w in watched:
            row = rows.get(w["market_id"])
            if row is None:
                continue
            checked += 1
            verdict = _decide(row)
            if verdict is None:
                continue
            side, diff = verdict
            if not _should_fire(w["market_id"], side, diff):
                continue
            _mark_fired(w["market_id"], side, diff)
            fired += 1
            title = row.get("market_title") or w["market_title"] or w["market_id"]
            with SessionLocal() as s:
                s.add(LiveSignal(
                    match_id=match.match_id, market_id=w["market_id"],
                    market_title=title, side=side,
                    live_probability=row["live_model_probability"],
                    market_probability=row["market_probability"],
                    difference=round(diff, 4),
                    minute=minute))
                s.commit()
            emoji = "🟢" if side == "BUY" else "🔴"
            min_str = f" ({minute:.0f}')" if minute is not None else ""
            send_discord(
                f"{emoji} **{side} SIGNAL** — {match.home} vs {match.away}{min_str}\n"
                f"**{title}**\n"
                f"Live model {row['live_model_probability']:.0%} vs "
                f"market {row['market_probability']:.0%} "
                f"({diff:+.0%})")

    return {"checked": checked, "fired": fired}
