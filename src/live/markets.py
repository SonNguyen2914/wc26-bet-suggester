"""Kalshi market discovery, approved-alias mapping, and full-book
capture for the live plane (launch decision O6).

Mapping rule: an event attaches to a fixture ONLY when (a) the ticker's
ET-date segment matches the fixture's kickoff date and (b) BOTH title
sides resolve through APPROVED kalshi aliases to the fixture's teams.
Anything else stays unmapped and is reported by readiness. Quotes are
integer cents, both sides with sizes, plus order-book depth, each tied
to a content-hashed source observation.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from src.live import identity
from src.live.db import get_session, plane_ready
from src.live.models import (Fixture, MarketContract, MarketDepthLevel,
                             MarketEvent, MarketQuote, SourceObservation)

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXMLSGAME"

_OUTCOME_BY_ORDER = ("home_win", "draw", "away_win")   # not relied upon


def _now():
    return datetime.now(timezone.utc)


def _cents(m: dict, field: str) -> int | None:
    """Kalshi's native integer-cent field first (fixed point, per the
    decision); the *_dollars string only as fallback."""
    v = m.get(field)
    if isinstance(v, int):
        return v
    try:
        return int(round(float(m.get(f"{field}_dollars")) * 100))
    except (TypeError, ValueError):
        return None


def _ticker_date(event_ticker: str) -> str | None:
    m = re.match(rf"{SERIES}-(\d{{2}}[A-Z]{{3}}\d{{2}})", event_ticker or "")
    return m.group(1) if m else None


def _ticker_day(tdate: str | None) -> date | None:
    """'26JUL25' -> date(2026, 7, 25)."""
    if not tdate:
        return None
    try:
        return datetime.strptime(tdate, "%y%b%d").date()
    except ValueError:
        return None


# Kalshi rate-limits bursts hard (429/503 seen live Jul 23 on a tight
# contract-fetch loop). Every market-list request goes through this.
_MIN_GAP_S = 0.25
_last_call = 0.0


def _kalshi_get(url: str, **kw):
    global _last_call
    wait = _MIN_GAP_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    r = requests.get(url, timeout=15, **kw)
    r.raise_for_status()
    return r.json()


def _fixture_et_date(dt) -> str:
    et = dt.astimezone(timezone(timedelta(hours=-4)))
    return et.strftime("%y%b%d").upper()


def _try_map(s, row: MarketEvent, tdate: str | None) -> bool:
    """Attach an event to its fixture via the approved-alias rule.
    Retried on later sweeps: an event can arrive before its fixture, or
    before an alias fix lands."""
    title = row.title or ""
    if " vs " not in title or not tdate:
        return False
    k_home, k_away = title.split(" vs ", 1)
    home = identity.resolve("kalshi", k_home.strip())
    away = identity.resolve("kalshi", k_away.strip())
    if not (home and away):
        return False
    for f in (s.query(Fixture)
              .filter_by(competition_slug="mls-2026",
                         home_team_id=home.id,
                         away_team_id=away.id).all()):
        if (f.current_kickoff_utc and _fixture_et_date(
                f.current_kickoff_utc.replace(
                    tzinfo=f.current_kickoff_utc.tzinfo
                    or timezone.utc)) == tdate):
            row.fixture_id = f.id
            row.mapped_via = "alias"
            row.mapping_approved = True
            return True
    return False


def _ensure_contracts(s, row: MarketEvent) -> None:
    """Create contract rows (side label -> outcome key) for an event
    that has none yet. Throttled; failures retry next sweep."""
    payload = _kalshi_get(f"{KALSHI}/markets",
                          params={"event_ticker": row.kalshi_event_ticker,
                                  "limit": 20})
    fx = s.get(Fixture, row.fixture_id) if row.fixture_id else None
    for m in payload.get("markets") or []:
        label = (m.get("yes_sub_title") or m.get("title") or "").strip()
        okey = None
        if label.lower() == "tie":
            okey = "draw"
        else:
            t = identity.resolve("kalshi", label)
            if t and fx:
                if t.id == fx.home_team_id:
                    okey = "home_win"
                elif t.id == fx.away_team_id:
                    okey = "away_win"
        if not s.query(MarketContract).filter_by(
                ticker=m.get("ticker")).first():
            s.add(MarketContract(market_event_id=row.id,
                                 ticker=m.get("ticker"),
                                 side_label=label, outcome_key=okey))


def discover_and_map() -> dict:
    """Fetch the KXMLSGAME event list; attach events to fixtures via the
    approved-alias rule; ensure contract rows exist for every current or
    future event. Historical events are recorded but their contracts are
    not fetched (rate-limit budget goes to the live slate)."""
    if not plane_ready():
        return {"skipped": "dormant"}
    try:
        events = _kalshi_get(f"{KALSHI}/events",
                             params={"series_ticker": SERIES,
                                     "limit": 100}).get("events") or []
    except requests.RequestException as exc:
        print(f"[markets] discovery failed: {exc}")
        return {"error": str(exc)[:200]}
    s = get_session()
    mapped = unmapped = contracts_filled = 0
    horizon_floor = (_now() - timedelta(days=1)).date()
    try:
        for ev in events:
            ticker = ev.get("event_ticker")
            if not ticker:
                continue
            tdate = _ticker_date(ticker)
            row = s.query(MarketEvent).filter_by(
                kalshi_event_ticker=ticker).first()
            if row is None:
                row = MarketEvent(competition_slug="mls-2026",
                                  kalshi_event_ticker=ticker,
                                  series=SERIES,
                                  title=ev.get("title") or "",
                                  settlement_scope="regular_time",
                                  mapping_approved=False)
                s.add(row)
            if not row.mapping_approved:
                if _try_map(s, row, tdate):
                    mapped += 1
                else:
                    unmapped += 1
            s.flush()
            day = _ticker_day(tdate)
            if (day and day >= horizon_floor
                    and not s.query(MarketContract)
                    .filter_by(market_event_id=row.id).first()):
                try:
                    _ensure_contracts(s, row)
                    contracts_filled += 1
                except requests.RequestException as exc:
                    print(f"[markets] contracts {ticker}: {exc}")
        s.commit()
        return {"events_seen": len(events), "newly_mapped": mapped,
                "unmapped": unmapped,
                "contracts_filled": contracts_filled}
    except Exception as exc:
        s.rollback()
        print(f"[markets] mapping failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def capture_quotes(fixture_id: int | None = None,
                   horizon_hours: float = 48.0) -> dict:
    """Full-book snapshots for mapped events whose fixtures kick off
    within the horizon (or one fixture when given). Cents + sizes both
    sides + depth, hash-chained to a source observation."""
    if not plane_ready():
        return {"skipped": "dormant"}
    s = get_session()
    quotes = 0
    try:
        q = (s.query(MarketEvent)
             .filter_by(competition_slug="mls-2026",
                        mapping_approved=True)
             .filter(MarketEvent.fixture_id.isnot(None)))
        events = []
        for me in q.all():
            fx = s.get(Fixture, me.fixture_id)
            if fixture_id is not None and me.fixture_id != fixture_id:
                continue
            if fx is None or fx.current_kickoff_utc is None:
                continue
            ko = fx.current_kickoff_utc
            ko = ko if ko.tzinfo else ko.replace(tzinfo=timezone.utc)
            if fixture_id is None and not (
                    -3 <= (ko - _now()).total_seconds() / 3600
                    <= horizon_hours):
                continue
            events.append(me)
        for me in events:
            try:
                payload = _kalshi_get(
                    f"{KALSHI}/markets",
                    params={"event_ticker": me.kalshi_event_ticker,
                            "limit": 20})
            except requests.RequestException as exc:
                print(f"[markets] quotes {me.kalshi_event_ticker}: {exc}")
                continue
            raw = json.dumps(payload, sort_keys=True)
            obs = SourceObservation(
                source="kalshi",
                endpoint=f"markets?event={me.kalshi_event_ticker}",
                content_hash=hashlib.sha256(raw.encode()).hexdigest(),
                payload_json=raw[:200_000], observed_at=_now())
            s.add(obs)
            s.flush()
            for m in payload.get("markets") or []:
                mc = s.query(MarketContract).filter_by(
                    ticker=m.get("ticker")).first()
                if mc is None:
                    continue
                quote = MarketQuote(
                    market_contract_id=mc.id, captured_at=_now(),
                    yes_bid_c=_cents(m, "yes_bid"),
                    yes_ask_c=_cents(m, "yes_ask"),
                    no_bid_c=_cents(m, "no_bid"),
                    no_ask_c=_cents(m, "no_ask"),
                    last_trade_c=_cents(m, "last_price"),
                    volume=m.get("volume"),
                    open_interest=m.get("open_interest"),
                    status=m.get("status"),
                    source_observation_id=obs.id)
                s.add(quote)
                s.flush()
                # order-book depth (best-effort; absence is fine)
                try:
                    ob = _kalshi_get(
                        f"{KALSHI}/markets/{m.get('ticker')}/orderbook"
                    ).get("orderbook") or {}
                    for side in ("yes", "no"):
                        for lvl in (ob.get(side) or [])[:5]:
                            if isinstance(lvl, (list, tuple)) \
                                    and len(lvl) >= 2:
                                s.add(MarketDepthLevel(
                                    market_quote_id=quote.id, side=side,
                                    price_c=int(lvl[0]),
                                    size=int(lvl[1])))
                except Exception:
                    pass
                quotes += 1
        s.commit()
        return {"events": len(events), "quotes": quotes}
    except Exception as exc:
        s.rollback()
        print(f"[markets] capture failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()
