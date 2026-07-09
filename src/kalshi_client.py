"""Kalshi market client — demo mode + live discovery mode.

LIVE MODE (DEMO_MODE=false):
  Uses Kalshi's public trade API v2. Market data endpoints are public
  (no auth needed for reads). Discovery works by:
    1. GET /events?status=open (paginated, politely) — cached for 10 min
    2. keep events whose title mentions BOTH team names of a match
    3. GET /markets?event_ticker=... for each matching event
    4. map each market to an outcome_key by inspecting its title/subtitle

  Kalshi rate-limits unauthenticated requests, so every call here pauses
  between pages and backs off + retries on HTTP 429.

  Because Kalshi's exact WC26 ticker scheme isn't documented, run
  `python scripts/probe_kalshi.py` once to see the real events/markets
  and tweak `_classify_outcome` if any titles don't map.

DEMO MODE (DEMO_MODE=true):
  Generates realistic, slowly-drifting mock markets so everything runs
  with zero keys.

Both modes return the same shape:
  {market_id, match_id, title, outcome_key, yes_price (0-1),
   decimal_odds, volume_24h, status}
"""
from __future__ import annotations

import hashlib
import math
import re
import time

import requests

import config
from src.schedule_data import Match, get_team_stats, load_schedule


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def price_to_decimal_odds(yes_price: float) -> float:
    yes_price = min(max(yes_price, 0.01), 0.99)
    return round(1.0 / yes_price, 2)


# Team-name aliases so event-title matching survives Kalshi's phrasing.
ALIASES: dict[str, list[str]] = {
    "United States": ["united states", "usa", "usmnt", "u.s."],
    "Switzerland": ["switzerland", "swiss"],
}

# FIFA three-letter codes as used inside Kalshi tickers (e.g. FRA1MAR0,
# ESP2, SUIREG). Needed because several codes are NOT a prefix of the
# team name (SUI/Switzerland, ESP/Spain, NED/Netherlands, GER/Germany...),
# so prefix matching alone can't resolve which team a ticker code names.
FIFA_CODES: dict[str, str] = {
    "Morocco": "MAR", "France": "FRA", "Spain": "ESP", "Belgium": "BEL",
    "Norway": "NOR", "England": "ENG", "Argentina": "ARG",
    "Switzerland": "SUI", "Brazil": "BRA", "Portugal": "POR",
    "Mexico": "MEX", "United States": "USA", "Egypt": "EGY",
    "Colombia": "COL", "Netherlands": "NED", "Germany": "GER",
    "Croatia": "CRO", "Italy": "ITA", "Japan": "JPN", "Senegal": "SEN",
    "Uruguay": "URU", "Denmark": "DEN", "IR Iran": "IRN", "Austria": "AUT",
    "Paraguay": "PAR", "Canada": "CAN",
}


def _name_variants(team: str) -> list[str]:
    return ALIASES.get(team, [team.lower()])


def _code_is(code: str, team: str) -> bool:
    """Does a ticker team-code (FRA, SUI, ESP...) name this team? Exact FIFA
    code first, then name-prefix fallback for codes that ARE a prefix
    (MAR->Morocco). Never guesses: an unrecognized code matches nothing."""
    code = code.strip().upper()
    if not code:
        return False
    if FIFA_CODES.get(team, "").upper() == code:
        return True
    return any(v.upper().startswith(code) for v in _name_variants(team)
               if len(code) >= 3)


def _get_with_backoff(session: requests.Session, url: str, params: dict,
                      max_retries: int = 5) -> requests.Response:
    """GET with polite handling of Kalshi rate limits: on 429, wait
    (respecting Retry-After if present) and retry with growing pauses."""
    delay = 2.0
    for attempt in range(max_retries):
        resp = session.get(url, params=params, timeout=15)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp
        wait = float(resp.headers.get("Retry-After", delay))
        time.sleep(wait)
        delay = min(delay * 2, 15.0)
    resp.raise_for_status()  # raise the final 429
    return resp


# ---------------------------------------------------------------------------
# Demo market generator (unchanged behavior)
# ---------------------------------------------------------------------------
def _drift(seed: str, base: float, amplitude: float = 0.03) -> float:
    t = time.time() / 1800.0
    phase = int(hashlib.md5(seed.encode()).hexdigest()[:6], 16) % 628 / 100.0
    return base + amplitude * math.sin(t + phase)


def _rough_market_probs(match: Match) -> dict[str, float]:
    home, away = get_team_stats(match.home), get_team_stats(match.away)
    diff = home["elo"] - away["elo"]
    p_home = 1.0 / (1.0 + 10 ** (-diff / 400.0))
    p_draw = 0.24 - abs(p_home - 0.5) * 0.18
    return {"home_win": p_home * (1 - p_draw), "draw": p_draw,
            "away_win": (1 - p_home) * (1 - p_draw)}


def _demo_markets_for_match(match: Match) -> list[dict]:
    probs = _rough_market_probs(match)
    vig = 1.05
    rows = [
        ("home_win", f"{match.home} to win", probs["home_win"]),
        ("away_win", f"{match.away} to win", probs["away_win"]),
        ("draw", "Match ends in a draw", probs["draw"]),
        ("over_2_5", "Over 2.5 total goals", 0.52),
        ("under_2_5", "Under 2.5 total goals", 0.48),
        ("btts", "Both teams to score", 0.50),
        ("home_2_0", f"{match.home} wins exactly 2-0", probs["home_win"] * 0.28),
        ("home_1_0", f"{match.home} wins exactly 1-0", probs["home_win"] * 0.30),
    ]
    markets = []
    for key, title, p in rows:
        seed = f"{match.match_id}:{key}"
        yes = min(max(_drift(seed, p * vig), 0.03), 0.95)
        vol_seed = int(hashlib.md5(seed.encode()).hexdigest()[:5], 16)
        markets.append({
            "market_id": f"WC26-{match.match_id}-{key.upper()}",
            "match_id": match.match_id,
            "title": title,
            "outcome_key": key,
            "yes_price": round(yes, 3),
            "decimal_odds": price_to_decimal_odds(yes),
            "volume_24h": 8000 + vol_seed % 250_000,
            "status": "open",
        })
    return markets


# ---------------------------------------------------------------------------
# Live mode: event discovery + market mapping
# ---------------------------------------------------------------------------
_events_cache: dict = {"at": 0.0, "events": []}
EVENTS_CACHE_TTL = 600  # 10 min


def _fetch_all_open_events(session: requests.Session) -> list[dict]:
    """Paginate through open events, politely: pause between pages and
    back off on 429s. Cached for 10 minutes so the cost is paid rarely."""
    now = time.time()
    if now - _events_cache["at"] < EVENTS_CACHE_TTL and _events_cache["events"]:
        return _events_cache["events"]

    events, cursor = [], None
    for _ in range(60):  # hard page cap
        params = {"status": "open", "limit": 200}
        if cursor:
            params["cursor"] = cursor

        resp = _get_with_backoff(session, f"{config.KALSHI_BASE_URL}/events", params)
        data = resp.json()
        events.extend(data.get("events", []))
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(0.7)  # be polite between pages

    _events_cache.update(at=now, events=events)
    return events


def _events_for_match(events: list[dict], match: Match) -> list[dict]:
    """Events whose title mentions both teams (any alias)."""
    home_vs = _name_variants(match.home)
    away_vs = _name_variants(match.away)
    out = []
    for ev in events:
        title = (ev.get("title") or "").lower()
        if any(h in title for h in home_vs) and any(a in title for a in away_vs):
            out.append(ev)
    return out


_SCORE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")

# Kalshi WC26 event families we deliberately skip: our 90-min match simulator
# can't price half-specific, corner, player-prop, or novelty markets.
SKIP_FAMILIES = (
    "KXWCMENTION", "KXWCDELAY", "KXWCSOA", "KXWCTCORNERS", "KXWCCORNERS",
    "KXWC1HTOTAL", "KXWC1HSPREAD", "KXWC1HBTTS",
    "KXWC2HTOTAL", "KXWC2HSPREAD", "KXWC2HBTTS",
    "KXWC1HSCORE", "KXWC2HSCORE",
    "KXWCTEAMTOTAL", "KXWCSTART",
    # KXWCFTTS = "Team to score first" (resolves on the game's OPENING goal,
    # incl. extra time — per Kalshi's own market rules). NOT a winner market;
    # the text fallback was mislabeling it as home/away_win. Our 90-min sim
    # doesn't model goal order, so skip until a real first-goal model exists.
    "KXWCFTTS",
    # KXWCTEAMFIRSTGOAL = per-PLAYER first-goalscorer props (e.g.
    # ...-MEX-ELIRA6 = "E. Lira scores first"). Their titles name one team,
    # so the text fallback labeled them home/away_win — and dedup then let a
    # 1-cent player prop silently replace the real moneyline (the
    # MEX_ENG 16.67x incident). No player model here; skip the family.
    "KXWCTEAMFIRSTGOAL",
)


def _event_family(event_ticker: str) -> str:
    return event_ticker.split("-")[0].upper()


def _classify_outcome(match: Match, market: dict,
                      event_ticker: str = "") -> str | None:
    """Map a Kalshi market to one of our outcome keys.

    Primary signal: the event-ticker family (KXWCTOTAL, KXWCSPREAD, ...),
    which is unambiguous. Free-text matching is only the fallback for
    families we haven't seen (e.g. the moneyline/advance events).
    Returns None when we can't (or shouldn't) map it."""
    family = _event_family(event_ticker)
    ticker = (market.get("ticker") or "").upper()
    text = " ".join(filter(None, [
        market.get("title", ""), market.get("yes_sub_title", ""),
        market.get("subtitle", ""),
    ])).lower()

    if family in SKIP_FAMILIES:
        return None

    # Regulation-time total goals: suffix N means "over (N-1).5"
    if family == "KXWCTOTAL":
        m = re.search(r"-(\d+)$", ticker)
        if m:
            line = int(m.group(1)) - 1
            return f"over_{line}_5" if 0 <= line <= 5 else None
        return None

    # Regulation-time spread: "<TEAM><N>" suffix means "wins by more than (N-0.5)"
    if family == "KXWCSPREAD":
        m = re.search(r"-([A-Z]+?)(\d+)$", ticker)
        if not m:
            return None
        margin = int(m.group(2))
        if margin not in (2, 3):
            return None
        # Side codes are FIFA codes (FRA2, SUI3...) — resolve via _code_is,
        # which handles non-prefix codes (SUI/Switzerland, ESP/Spain) that the
        # old prefix-fragment match silently dropped.
        side_text = m.group(1)
        if _code_is(side_text, match.home):
            return f"home_margin_{margin}"
        if _code_is(side_text, match.away):
            return f"away_margin_{margin}"
        return None

    if family == "KXWCBTTS":
        return "btts"

    # Exact final score: ticker suffix like "FRA1MAR0" = France 1, Morocco 0.
    # The goals belong to the NAMED team codes — Kalshi's team order in the
    # ticker is its own (often not our home/away order), so the codes must be
    # resolved to our sides before building score_<home>_<away>. Mapping the
    # digits positionally flipped every asymmetric score whenever Kalshi
    # listed our away team first (e.g. FRAMAR with Morocco as our home).
    if family == "KXWCSCORE":
        m = re.search(r"-([A-Z]+?)(\d+)([A-Z]+?)(\d+)$", ticker)
        if not m:
            return None
        c1, g1, c2, g2 = m.groups()
        if _code_is(c1, match.home) or _code_is(c2, match.away):
            return f"score_{g1}_{g2}"
        if _code_is(c1, match.away) or _code_is(c2, match.home):
            return f"score_{g2}_{g1}"
        return None  # codes unrecognized — skip rather than misprice

    # Method of victory: REG = wins in regulation = our 90-min outcome.
    # ET/PEN (extra time / penalties) can't be priced by a 90-min sim: skip.
    if family == "KXWCMOV":
        if ticker.endswith("REG"):
            side = ticker.rsplit("-", 1)[-1][:-3]  # strip 'REG' -> FIFA code
            if _code_is(side, match.home):
                return "home_win"
            if _code_is(side, match.away):
                return "away_win"
        return None

    # ---- Fallback for unknown families (moneyline / draw / advance) ----
    # Guards FIRST: never map partial-match or prop language via fallback,
    # no matter what the subtitle says (a "1st half Draw 2-2" market must
    # not become our full-match draw).
    if any(bad in text for bad in ("corner", "1st half", "2nd half",
                                   "first half", "second half", "wins by",
                                   "score over", "card", "announcer",
                                   "half score", "halftime", "half-time",
                                   "first goal", "goalscorer", "goal scorer",
                                   "score first", "scores first",
                                   "first to score")):
        return None

    # For winner-style events the market title names BOTH teams
    # ("Brazil vs Norway Winner?"); the side lives in yes_sub_title or the
    # ticker suffix. Check those alone first.
    sub = (market.get("yes_sub_title") or market.get("subtitle") or "").lower()
    suffix = ticker.rsplit("-", 1)[-1].lower() if "-" in ticker else ""

    if "draw" in sub or "tie" in sub or suffix in ("tie", "draw"):
        return "draw"

    home_sub = any(v in sub for v in _name_variants(match.home)) or \
        any(v.startswith(suffix) for v in _name_variants(match.home) if len(suffix) >= 3)
    away_sub = any(v in sub for v in _name_variants(match.away)) or \
        any(v.startswith(suffix) for v in _name_variants(match.away) if len(suffix) >= 3)

    home_hit = any(v in text for v in _name_variants(match.home))
    away_hit = any(v in text for v in _name_variants(match.away))

    if "draw" in text and not (home_sub or away_sub):
        return "draw"

    is_advance = "advance" in text or "qualify" in text

    if home_sub and not away_sub:
        return "home_advance" if is_advance else "home_win"
    if away_sub and not home_sub:
        return "away_advance" if is_advance else "away_win"

    m = _SCORE_RE.search(text)
    if m and home_hit and not away_hit:
        return f"home_{m.group(1)}_{m.group(2)}"

    if is_advance:
        if home_hit and not away_hit:
            return "home_advance"
        if away_hit and not home_hit:
            return "away_advance"
        return None
    # Plain winner market: exactly one team named anywhere
    if home_hit and not away_hit:
        return "home_win"
    if away_hit and not home_hit:
        return "away_win"
    return None


def _to_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _mid(bid: float | None, ask: float | None) -> float | None:
    """Midpoint of a two-sided quote; rejects empty/degenerate books."""
    if bid is None or ask is None:
        return None
    if ask <= 0 or ask > 1 or bid < 0:
        return None
    if bid <= 0.001 and ask >= 0.999:   # empty book placeholder
        return None
    return (bid + ask) / 2


def _market_yes_price(m: dict) -> float | None:
    """Best available yes-price as probability 0-1.

    Kalshi's fractional markets (all WC26 match markets) report prices in
    *_dollars string fields; the legacy integer-cent fields sit null.
    Priority: yes-side dollars quote → derived from no-side dollars quote
    (yes = 1 - no) → last traded price → legacy integer cents."""
    # 1. yes-side dollars quote
    p = _mid(_to_float(m.get("yes_bid_dollars")), _to_float(m.get("yes_ask_dollars")))
    if p is not None:
        return round(p, 3)
    # 2. derive from no-side dollars quote
    no_mid = _mid(_to_float(m.get("no_bid_dollars")), _to_float(m.get("no_ask_dollars")))
    if no_mid is not None:
        return round(1.0 - no_mid, 3)
    # 3. last traded price (dollars string, then legacy cents)
    last = _to_float(m.get("last_price_dollars"))
    if last is not None and 0 < last < 1:
        return round(last, 3)
    # 4. legacy integer-cent fields
    bid, ask = m.get("yes_bid"), m.get("yes_ask")
    if bid and ask and ask > 0:
        return round((bid + ask) / 2 / 100.0, 3)
    last = m.get("last_price")
    if last:
        return round(last / 100.0, 3)
    return None


def _market_volume(m: dict) -> float:
    """Best available liquidity signal. Fractional markets report *_fp
    string fields; fall back through them, ending with open interest."""
    for key in ("volume_24h", "volume_24h_fp", "volume", "volume_fp",
                "open_interest", "open_interest_fp"):
        v = _to_float(m.get(key))
        if v:
            return v
    return 0.0


def _display_title(match: Match, outcome_key: str, fallback: str) -> str:
    """Kalshi's event titles are ambiguous ('X vs Y: To Advance' never names
    the side). Build an explicit title from what we actually classified."""
    h, a = match.home, match.away
    fixed = {
        "home_win": f"{h} to win (90 min)",
        "away_win": f"{a} to win (90 min)",
        "draw": "Draw after 90 min",
        "home_advance": f"{h} to advance",
        "away_advance": f"{a} to advance",
        "btts": "Both teams to score",
        "home_margin_2": f"{h} to win by 2+ goals",
        "home_margin_3": f"{h} to win by 3+ goals",
        "away_margin_2": f"{a} to win by 2+ goals",
        "away_margin_3": f"{a} to win by 3+ goals",
    }
    if outcome_key in fixed:
        return fixed[outcome_key]
    m = re.match(r"over_(\d)_5$", outcome_key)
    if m:
        return f"Over {m.group(1)}.5 total goals"
    m = re.match(r"under_(\d)_5$", outcome_key)
    if m:
        return f"Under {m.group(1)}.5 total goals"
    m = re.match(r"score_(\d+)_(\d+)$", outcome_key)
    if m:
        hg, ag = m.group(1), m.group(2)
        if hg == ag:
            return f"Exact score: {hg}-{ag} draw"
        return f"Exact score: {h} {hg}-{ag} {a}" if int(hg) > int(ag) \
            else f"Exact score: {a} {ag}-{hg} {h}"
    return fallback


# ---------------------------------------------------------------------------
class KalshiClient:
    def __init__(self):
        self.demo = config.DEMO_MODE
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "wc26-suggester/0.3 (read-only research)"

    def get_markets_for_match(self, match: Match) -> list[dict]:
        if self.demo:
            return _demo_markets_for_match(match)
        try:
            return self._live_markets_for_match(match)
        except requests.RequestException as exc:
            print(f"[kalshi] live fetch failed for {match.match_id}: {exc}")
            return []

    def get_all_open_markets(self) -> list[dict]:
        out: list[dict] = []
        for match in load_schedule():
            out.extend(self.get_markets_for_match(match))
        return out

    # -- live -----------------------------------------------------------
    def _live_markets_for_match(self, match: Match) -> list[dict]:
        events = _fetch_all_open_events(self.session)
        matched_events = _events_for_match(events, match)
        markets: list[dict] = []
        for ev in matched_events:
            if _event_family(ev["event_ticker"]) in SKIP_FAMILIES:
                continue  # don't waste a request on families we can't price
            resp = _get_with_backoff(
                self.session,
                f"{config.KALSHI_BASE_URL}/markets",
                {"event_ticker": ev["event_ticker"], "status": "open", "limit": 100},
            )
            for m in resp.json().get("markets", []):
                outcome = _classify_outcome(match, m, ev["event_ticker"])
                if outcome is None:
                    continue
                yes = _market_yes_price(m)
                if yes is None:
                    continue  # empty order book (common far from kickoff)
                markets.append({
                    "market_id": m["ticker"],
                    "match_id": match.match_id,
                    "title": _display_title(match, outcome,
                                             m.get("title") or m["ticker"]),
                    "outcome_key": outcome,
                    "yes_price": yes,
                    "decimal_odds": price_to_decimal_odds(yes),
                    "volume_24h": _market_volume(m),
                    "status": m.get("status", "open"),
                })
            time.sleep(0.4)  # polite gap between per-event market fetches
        return markets
