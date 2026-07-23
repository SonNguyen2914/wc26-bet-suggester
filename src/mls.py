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


TRADEABLE = ("active", "open", "initialized")


def _game_events(limit: int = 60) -> list[dict]:
    """The KXMLSGAME event list — one cheap call, 120s cache. NO status
    filter: an in-play fixture's event stops reporting "open" while its
    markets keep trading as "active" (found live on MLS night one — the
    CLB-NYC book was active at 38/33/30 while the open-filtered list
    omitted it). Tradability is judged per MARKET, at market-fetch time."""
    def fetch():
        d = _get_json(f"{KALSHI_BASE}/events",
                      {"series_ticker": KALSHI_MLS_GAME, "limit": limit})
        return (d.get("events") or []) if d else None
    return _cached("events", 120, fetch) or []


def event_markets(event_ticker: str) -> list[dict]:
    """One event's tradeable markets, 15s cache — cheap enough for the
    match page's 30s poll to ride."""
    def fetch():
        md = _get_json(f"{KALSHI_BASE}/markets",
                       {"event_ticker": event_ticker, "limit": 20})
        if md is None:
            return None
        return [m for m in (md.get("markets") or [])
                if m.get("status") in TRADEABLE]
    return _cached(f"mkts:{event_ticker}", 15, fetch) or []


def game_books(limit: int = 60) -> list[dict]:
    """Every fixture's tradeable book (the dashboard grid). Assembled
    from the shared event list + per-event market caches, so the match
    page and the dashboard amortize the same fetches."""
    events = _game_events(limit)
    markets = {ev["event_ticker"]: event_markets(ev["event_ticker"])
               for ev in events}
    books = parse_game_books(events, markets)
    return [b for b in books if b["markets"]]   # drop settled fixtures


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
        "scouting": {"last_five": _parse_last_five(d),
                     "head_to_head": _parse_h2h(d)},
    }


def parse_team_colors(d: dict) -> dict[str, dict]:
    """ESPN teams payload -> {abbrev: {color, alt}} hex strings."""
    out = {}
    leagues = (d.get("sports") or [{}])[0].get("leagues") or [{}]
    for t in leagues[0].get("teams") or []:
        team = t.get("team") or {}
        ab = team.get("abbreviation")
        if ab:
            out[ab] = {"color": team.get("color"),
                       "alt": team.get("alternateColor")}
    return out


def team_colors() -> dict[str, dict]:
    """Club signature colors (1h cache — they change never)."""
    def fetch():
        d = _get_json(f"{ESPN_BASE}/teams")
        return parse_team_colors(d) if d else None
    return _cached("team_colors", 3600, fetch) or {}


def match_summary(event_id: str) -> dict | None:
    """One match's live stat page. 30s cache (it IS the live view)."""
    def fetch():
        d = _get_json(f"{ESPN_BASE}/summary", {"event": event_id})
        if not d:
            return None
        out = parse_summary(d)
        colors = team_colors()
        for side in ("home", "away"):
            c = colors.get((out.get(side) or {}).get("abbrev") or "")
            if c:
                out[side]["color"] = c.get("color")
                out[side]["alt_color"] = c.get("alt")
        return out
    return _cached(f"sum:{event_id}", 30, fetch)


# --- match hub: scouting + the fixture's own Kalshi book -------------------

def _parse_last_five(d: dict) -> list[dict]:
    """lastFiveGames -> per team: form string + recent results."""
    out = []
    for t in d.get("lastFiveGames") or []:
        evs = t.get("events") or []
        out.append({
            "team": (t.get("team") or {}).get("displayName"),
            "abbrev": (t.get("team") or {}).get("abbreviation"),
            "form": " ".join(e.get("gameResult", "?") for e in evs[:5]),
            "games": [{
                "result": e.get("gameResult"),
                "score": e.get("score"),
                "at_vs": e.get("atVs"),
                "opponent": (e.get("opponent") or {}).get("abbreviation"),
                "date": e.get("gameDate"),
            } for e in evs[:5]],
        })
    return out


def _parse_h2h(d: dict) -> list[dict]:
    """headToHeadGames -> recent meetings, from the first team's view."""
    groups = d.get("headToHeadGames") or []
    if not groups:
        return []
    team = (groups[0].get("team") or {}).get("abbreviation")
    out = []
    for e in (groups[0].get("events") or [])[:6]:
        out.append({
            "perspective": team,
            "result": e.get("gameResult"),
            "home_score": e.get("homeTeamScore"),
            "away_score": e.get("awayTeamScore"),
            "at_vs": e.get("atVs"),
            "opponent": (e.get("opponent") or {}).get("abbreviation"),
            "date": e.get("gameDate"),
        })
    return out


# Kalshi title-name -> ESPN displayName bridges that substring matching
# cannot cross. Verified against the live KXMLSGAME slate 2026-07-22.
_KALSHI_ALIASES = {
    "los angeles g": "la galaxy",
    "los angeles f": "los angeles fc",
    "saint louis": "st louis city",
    # ESPN's displayName is "Red Bull New York", NOT "New York Red
    # Bulls" — the old alias never matched, so every RBNY fixture
    # showed "no open book" (caught on Son's phone, RBNY-CLT Jul 25)
    "new york rb": "red bull new york",
}


def _norm_name(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace(".", "").strip()


def _side_matches(kalshi_side: str, espn_name: str) -> bool:
    k, e = _norm_name(kalshi_side), _norm_name(espn_name)
    if not k or not e:
        return False
    return k in e or _KALSHI_ALIASES.get(k, "\x00") in e


def _ticker_et_date(event_ticker: str) -> str | None:
    """KXMLSGAME-26JUL25SJLAG -> '26JUL25' (Kalshi dates are US-Eastern)."""
    import re
    m = re.match(r"KXMLSGAME-(\d{2}[A-Z]{3}\d{2})", event_ticker or "")
    return m.group(1) if m else None


def _fixture_et_date(iso_date: str) -> str | None:
    """ESPN UTC kickoff -> the Kalshi-style US-Eastern date segment."""
    from datetime import datetime, timedelta, timezone
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    et = dt.astimezone(timezone(timedelta(hours=-4)))     # EDT (season)
    return et.strftime("%y%b%d").upper()


def find_book(fixture_date: str, home_name: str, away_name: str,
              books: list[dict] | None = None) -> dict | None:
    """This fixture's KXMLSGAME book: ET-date segment must match the
    ticker (teams meet twice inside one open window — SJ played Jul 22
    AND Jul 25 on the day this shipped), then both title sides must
    match the ESPN names."""
    want = _fixture_et_date(fixture_date)
    if books is not None:               # injected (tests)
        pool = books
    else:
        # two cheap calls instead of a full sweep: match on the EVENT
        # list first, then fetch only this fixture's markets
        pool = [{"event_ticker": ev.get("event_ticker"),
                 "title": ev.get("title"), "markets": None}
                for ev in _game_events()]
    for b in pool:
        if _ticker_et_date(b.get("event_ticker", "")) != want:
            continue
        title = b.get("title") or ""
        if " vs " not in title:
            continue
        k_home, k_away = title.split(" vs ", 1)
        if _side_matches(k_home, home_name) and \
                _side_matches(k_away, away_name):
            if b["markets"] is None:
                b = parse_game_books(
                    [b], {b["event_ticker"]:
                          event_markets(b["event_ticker"])})[0]
            return b if b["markets"] else None
    return None
