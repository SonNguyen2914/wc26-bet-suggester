"""Post-match research snapshots.

The T-10 final lock freezes the MODEL's view of a match. This module
freezes the MARKET's view once the match is over: every Kalshi market on
the fixture, with its settlement result and closing book, stored raw.
Together with the OddsReading corpus that gives research three aligned
columns per market: model probability (locked), closing price (traded),
and what actually settled.

Discovery deliberately differs from the live pipeline: settled events
drop out of the open-events feed, so each priced family's events are
listed WITHOUT a status filter and matched by team names — which is also
what makes backfill possible after the fact (the MAR-FRA lesson:
closing data looked lost, but Kalshi keeps settled markets queryable).

Capture is one-shot per match (idempotent) and never raises into its
caller — a snapshot failure must not disturb result freezing.
"""
from __future__ import annotations

import json
import time

import requests as _rq

import config
from src.db import MarketClosing, SessionLocal, utcnow
from src.kalshi_client import _get_with_backoff, _name_variants

# The families the app prices — the ones whose closing state has research
# value against the locked model numbers. Player-prop and novelty families
# are excluded on purpose (no model number to line them up against).
FAMILIES = ("KXWCGAME", "KXWCMOV", "KXWCADVANCE", "KXWCSCORE",
            "KXWCTOTAL", "KXWCSPREAD", "KXWCBTTS", "KXWCFTTS")


def _fetch_family_markets(sess, family: str, home: str,
                          away: str) -> list[tuple[str, dict]]:
    """(event_ticker, raw market) pairs for one family's event on this
    fixture — any status, so settled books are included."""
    evs = _get_with_backoff(
        sess, f"{config.KALSHI_BASE_URL}/events",
        {"series_ticker": family, "limit": 200},
    ).json().get("events", [])
    home_vs, away_vs = _name_variants(home), _name_variants(away)
    out: list[tuple[str, dict]] = []
    for ev in evs:
        title = (ev.get("title") or "").lower()
        if not (any(h in title for h in home_vs)
                and any(a in title for a in away_vs)):
            continue
        mkts = _get_with_backoff(
            sess, f"{config.KALSHI_BASE_URL}/markets",
            {"event_ticker": ev["event_ticker"], "limit": 200},
        ).json().get("markets", [])
        out.extend((ev["event_ticker"], m) for m in mkts)
        time.sleep(0.4)                # polite gap, same as the live path
    return out


def capture_closing_snapshot(match) -> dict:
    """Capture (once) the closing/settlement state of every priced-family
    market on a match. Returns a small status dict; never raises."""
    try:
        with SessionLocal() as s:
            existing = (s.query(MarketClosing)
                        .filter(MarketClosing.match_id == match.match_id)
                        .count())
            if existing:
                return {"status": "exists", "markets": existing}

        sess = _rq.Session()
        rows: list[MarketClosing] = []
        now = utcnow()
        for family in FAMILIES:
            try:
                for ev_ticker, mkt in _fetch_family_markets(
                        sess, family, match.home, match.away):
                    rows.append(MarketClosing(
                        match_id=match.match_id,
                        market_id=mkt.get("ticker") or "?",
                        event_ticker=ev_ticker,
                        captured_at=now,
                        data_json=json.dumps(mkt),
                    ))
            except Exception as exc:   # one family must not sink the rest
                print(f"[research] {match.match_id} {family} failed: {exc}")

        if not rows:
            return {"status": "empty", "markets": 0}
        with SessionLocal() as s:
            s.add_all(rows)
            s.commit()
        print(f"[research] {match.match_id}: closing snapshot, "
              f"{len(rows)} markets")
        return {"status": "captured", "markets": len(rows)}
    except Exception as exc:
        print(f"[research] {match.match_id} snapshot failed: {exc}")
        return {"status": "error", "error": str(exc)}


def closing_rows(match_id: str) -> list[dict]:
    """Parsed snapshot rows for one match, trimmed to the research-useful
    fields (the full raw object stays in the DB)."""
    with SessionLocal() as s:
        rows = (s.query(MarketClosing)
                .filter(MarketClosing.match_id == match_id)
                .order_by(MarketClosing.market_id).all())
    out = []
    for r in rows:
        try:
            m = json.loads(r.data_json or "{}")
        except Exception:
            m = {}
        out.append({
            "market_id": r.market_id,
            "event_ticker": r.event_ticker,
            "captured_at": (r.captured_at.isoformat()
                            if r.captured_at else None),
            "title": m.get("title") or m.get("yes_sub_title"),
            "status": m.get("status"),
            "result": m.get("result"),          # yes | no | "" (unsettled)
            "yes_bid": m.get("yes_bid_dollars"),
            "yes_ask": m.get("yes_ask_dollars"),
            "last_price": m.get("last_price_dollars",
                                m.get("last_price")),
            "volume": m.get("volume"),
            "open_interest": m.get("open_interest"),
        })
    return out
