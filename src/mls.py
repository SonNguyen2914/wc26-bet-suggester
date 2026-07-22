"""MLS data layer — the first next-league surface (Jul 22, 2026).

Self-contained by design: nothing here touches the WC26 archive paths,
the DB, or the scheduler. Keyless ESPN (usa.1) carries fixtures, live
scores, and standings; Kalshi's public API carries the KXMLSGAME 3-way
books and KXMLSCUP futures. Everything is fetch->parse split so the
parsers are unit-testable on canned payloads, and every fetch runs
through a small TTL cache so the public endpoints cannot stampede the
providers.

Scope honesty: this is the DATA layer only — no model, no suggestions,
no persistence. The V7 Part H acceptance gates still stand before any
MLS model/trading work; this module exists so real data flows today.
"""
from __future__ import annotations

import time

import requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/usa.1/standings"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# family constants, verified live 2026-07-22: KXMLSGAME = 3-way
# (home/tie/away) per fixture; KXMLSCUP = championship futures
KALSHI_MLS_GAME = "KXMLSGAME"
KALSHI_MLS_CUP = "KXMLSCUP"

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fetch):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    data = fetch()
    if data is not None:               # never cache a failed answer
        _cache[key] = (now, data)
        return data
    return hit[1] if hit else None     # stale beats nothing


def _get_json(url: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        print(f"[mls] fetch failed {url}: {exc}")
        return None


# --- parsers (pure, canned-payload testable) -------------------------------

def parse_event(e: dict) -> dict:
    """One ESPN scoreboard event -> a normalized fixture card."""
    comp = (e.get("competitions") or [{}])[0]
    sides: dict[str, dict] = {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        rec = ""
        for r in c.get("records") or []:
            if r.get("summary"):
                rec = r["summary"]
                break
        sides[c.get("homeAway", "?")] = {
            "name": t.get("displayName"),
            "short": t.get("shortDisplayName"),
            "abbrev": t.get("abbreviation"),
            "logo": t.get("logo"),
            "score": c.get("score"),
            "record": rec,
        }
    status = e.get("status") or {}
    stype = status.get("type") or {}
    return {
        "id": e.get("id"),
        "date": e.get("date"),
        "state": stype.get("state"),           # pre | in | post
        "detail": stype.get("shortDetail"),
        "minute": status.get("displayClock"),
        "venue": (comp.get("venue") or {}).get("fullName"),
        "home": sides.get("home", {}),
        "away": sides.get("away", {}),
    }


def parse_standings(d: dict) -> list[dict]:
    """ESPN standings -> [{conference, entries: [...]}], rank-ordered."""
    out = []
    for group in d.get("children") or []:
        entries = []
        for entry in (group.get("standings") or {}).get("entries") or []:
            team = entry.get("team") or {}
            stats = {s.get("name"): s.get("value")
                     for s in entry.get("stats") or []}
            entries.append({
                "team": team.get("displayName"),
                "abbrev": team.get("abbreviation"),
                "logo": (team.get("logos") or [{}])[0].get("href")
                if team.get("logos") else None,
                "rank": stats.get("rank"),
                "played": stats.get("gamesPlayed"),
                "wins": stats.get("wins"),
                "losses": stats.get("losses"),
                "ties": stats.get("ties"),
                "points": stats.get("points"),
                "goals_for": stats.get("pointsFor"),
                "goals_against": stats.get("pointsAgainst"),
                "goal_diff": stats.get("pointDifferential"),
                "ppg": stats.get("ppg"),
            })
        entries.sort(key=lambda x: (x["rank"] is None, x["rank"]))
        out.append({"conference": group.get("name"), "entries": entries})
    return out


def parse_game_books(events: list[dict],
                     markets_by_event: dict[str, list[dict]]) -> list[dict]:
    """Kalshi KXMLSGAME events + their markets -> per-fixture 3-way books
    with BOTH sides of each book (ask to buy, bid to exit — the bid/ask
    discipline is day-one policy for every new competition)."""
    out = []
    for ev in events:
        ticker = ev.get("event_ticker")
        rows = []
        for m in markets_by_event.get(ticker, []):
            rows.append({
                "ticker": m.get("ticker"),
                "label": m.get("yes_sub_title") or m.get("title"),
                "yes_ask": m.get("yes_ask_dollars"),
                "yes_bid": m.get("yes_bid_dollars"),
                "status": m.get("status"),
            })
        out.append({"event_ticker": ticker, "title": ev.get("title"),
                    "markets": rows})
    return out


# --- fetchers --------------------------------------------------------------

def scoreboard(date: str | None = None) -> list[dict]:
    """Normalized fixtures for one day (YYYYMMDD; ESPN's default bucket
    when omitted). 60s cache."""
    params = {"dates": date} if date else None

    def fetch():
        d = _get_json(f"{ESPN_BASE}/scoreboard", params)
        return ([parse_event(e) for e in d.get("events") or []]
                if d else None)
    return _cached(f"sb:{date or 'today'}", 60, fetch) or []


def schedule(days: int = 7) -> list[dict]:
    """The next `days` days of fixtures (today inclusive), flattened and
    kickoff-ordered. 300s cache per day-bucket."""
    from datetime import datetime, timedelta, timezone
    out: list[dict] = []
    today = datetime.now(timezone.utc)
    for i in range(max(1, min(days, 14))):
        day = (today + timedelta(days=i)).strftime("%Y%m%d")
        def fetch(day=day):
            d = _get_json(f"{ESPN_BASE}/scoreboard", {"dates": day})
            return ([parse_event(e) for e in d.get("events") or []]
                    if d else None)
        out.extend(_cached(f"sb:{day}", 300, fetch) or [])
    seen: set[str] = set()
    uniq = [f for f in out
            if f["id"] not in seen and not seen.add(f["id"])]
    uniq.sort(key=lambda f: f.get("date") or "")
    return uniq


def standings() -> list[dict]:
    def fetch():
        d = _get_json(ESPN_STANDINGS)
        return parse_standings(d) if d else None
    return _cached("standings", 300, fetch) or []


def game_books(limit: int = 30) -> list[dict]:
    """Open KXMLSGAME books from Kalshi's public API. 60s cache."""
    def fetch():
        d = _get_json(f"{KALSHI_BASE}/events",
                      {"series_ticker": KALSHI_MLS_GAME, "limit": limit,
                       "status": "open"})
        if not d:
            return None
        events = d.get("events") or []
        markets: dict[str, list[dict]] = {}
        for ev in events:
            md = _get_json(f"{KALSHI_BASE}/markets",
                           {"event_ticker": ev["event_ticker"],
                            "limit": 20})
            markets[ev["event_ticker"]] = (md.get("markets") or []
                                           if md else [])
            time.sleep(0.2)            # polite gap, same as the WC path
        return parse_game_books(events, markets)
    return _cached("books", 60, fetch) or []


def cup_futures() -> list[dict]:
    """KXMLSCUP championship futures. 300s cache."""
    def fetch():
        d = _get_json(f"{KALSHI_BASE}/events",
                      {"series_ticker": KALSHI_MLS_CUP, "limit": 5,
                       "status": "open"})
        if not d:
            return None
        events = d.get("events") or []
        markets: dict[str, list[dict]] = {}
        for ev in events:
            md = _get_json(f"{KALSHI_BASE}/markets",
                           {"event_ticker": ev["event_ticker"],
                            "limit": 100})
            markets[ev["event_ticker"]] = (md.get("markets") or []
                                           if md else [])
            time.sleep(0.2)
        return parse_game_books(events, markets)
    return _cached("cup", 300, fetch) or []


# --- per-match summary (the stat page) -------------------------------------

# boxscore stats worth showing, in display order
STAT_ORDER = (
    ("possessionPct", "Possession %"),
    ("totalShots", "Shots"),
    ("shotsOnTarget", "On target"),
    ("wonCorners", "Corners"),
    ("foulsCommitted", "Fouls"),
    ("offsides", "Offsides"),
    ("yellowCards", "Yellow cards"),
    ("redCards", "Red cards"),
    ("saves", "Saves"),
)


def parse_summary(d: dict) -> dict:
    """ESPN event summary -> {header, stats, events}. Tolerant of missing
    sections (pre-match summaries carry empty boxscores)."""
    header = d.get("header") or {}
    comp = (header.get("competitions") or [{}])[0]
    sides: dict[str, dict] = {}
    id_to_side: dict[str, str] = {}
    for c in comp.get("competitors") or []:
        t = c.get("team") or {}
        side = c.get("homeAway", "?")
        id_to_side[str(t.get("id"))] = side
        sides[side] = {
            "name": t.get("displayName"), "abbrev": t.get("abbreviation"),
            "logo": t.get("logos", [{}])[0].get("href")
            if t.get("logos") else t.get("logo"),
            "score": c.get("score"),
        }
    status = comp.get("status") or {}
    stype = status.get("type") or {}

    by_side: dict[str, dict] = {}
    for team in (d.get("boxscore") or {}).get("teams") or []:
        side = id_to_side.get(str((team.get("team") or {}).get("id")))
        if side:
            by_side[side] = {s.get("name"): s.get("displayValue")
                             for s in team.get("statistics") or []}
    stats = []
    for key, label in STAT_ORDER:
        h = by_side.get("home", {}).get(key)
        a = by_side.get("away", {}).get(key)
        if h is not None or a is not None:
            stats.append({"key": key, "label": label, "home": h, "away": a})

    events = []
    for kev in d.get("keyEvents") or []:
        events.append({
            "minute": (kev.get("clock") or {}).get("displayValue"),
            "type": (kev.get("type") or {}).get("text"),
            "team": (kev.get("team") or {}).get("displayName"),
            "text": kev.get("text"),
            "scoring": bool(kev.get("scoringPlay")),
        })

    return {
        "id": str((header.get("id") or d.get("meta", {}).get("id") or "")),
        "date": comp.get("date"),
        "state": stype.get("state"),
        "detail": stype.get("shortDetail"),
        "minute": status.get("displayClock"),
        "venue": ((d.get("gameInfo") or {}).get("venue") or {}).get("fullName"),
        "home": sides.get("home", {}),
        "away": sides.get("away", {}),
        "stats": stats,
        "events": events,
    }


def match_summary(event_id: str) -> dict | None:
    """One match's live stat page. 30s cache (it IS the live view)."""
    def fetch():
        d = _get_json(f"{ESPN_BASE}/summary", {"event": event_id})
        return parse_summary(d) if d else None
    return _cached(f"sum:{event_id}", 30, fetch)
