"""Layer 2 — live match state from API-Football (api-sports.io).

Turns the manual live panel into an auto-populating one: fetches the real
score, elapsed minute, and red cards for a World Cup fixture so the model
can simulate from the actual state instead of a typed-in guess.

Budget discipline (free tier = 100 requests/day):
  - a HARD daily cap (default 90) blocks calls before the real limit, so a
    runaway loop can never exhaust the quota mid-tournament;
  - a short per-response cache means repeated reads of the same live match
    within a few seconds cost ONE request, not many;
  - the caller is expected to fetch only when needed (a user opening the
    live panel, or later a Kalshi-spike trigger), never on a tight poll.

Everything degrades gracefully: no key, over budget, no match found, or a
network error all return None, and the live panel falls back to manual
entry. The feed AUGMENTS manual entry; it never replaces the ability to
type the state yourself.
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, datetime, timezone

import requests

import config

# --- daily budget counter (process-local; resets on UTC date change) -------
_call_date: date | None = None
_calls_today = 0

# --- shared response cache --------------------------------------------------
# Holds the raw /fixtures?live=all list under ONE key (_LIVE_ALL_KEY) so every
# live_state_for() lookup in a poll cycle reuses a SINGLE API call. (Previously
# this cached per team-pair and each pair made its own call, so a poll cycle
# with N matches cost N calls and drained the daily budget before kickoff.)
_cache: dict[str, tuple[float, list]] = {}
_LIVE_ALL_KEY = "__live_all__"

# Statuses API-Football reports for an in-progress match.
_LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
_FINISHED_STATUSES = {"FT", "AET", "PEN"}

# Our schedule's team names -> the name API-Football uses, when they differ.
# National-team naming is mostly identical, but a few diverge. Confirmed
# live: our "United States" is their "USA". Add here as new mismatches are
# found (matching is normalized, so only real spelling differences matter).
_TEAM_ALIASES = {
    "united states": "usa",
    "south korea": "korea republic",
    "north korea": "korea dpr",
    "ivory coast": "cote divoire",
    "czech republic": "czechia",
}


def _norm(name: str) -> str:
    """Fold accents/case/punctuation so 'Côte d'Ivoire' matches 'Cote
    dIvoire' etc., then apply any known alias (our name -> API-Football's).
    National-team names are usually identical across sources; this handles
    the accents, spacing, and the handful of genuine spelling differences."""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = "".join(c for c in n.lower() if c.isalnum() or c == " ").strip()
    # alias check on the human-readable lowercased form, before stripping spaces
    aliased = _TEAM_ALIASES.get(n, n)
    return "".join(c for c in aliased if c.isalnum())


def budget_status() -> dict:
    """Expose the daily counter so the API/UI can show remaining calls and
    explain a graceful fallback ('feed budget reached')."""
    _roll_date()
    return {
        "calls_today": _calls_today,
        "daily_cap": config.API_FOOTBALL_DAILY_CAP,
        "remaining": max(0, config.API_FOOTBALL_DAILY_CAP - _calls_today),
        "key_configured": bool(config.API_FOOTBALL_KEY),
        # keyless ESPN failover keeps live state flowing when the budget is
        # gone — the cap can no longer blind the app mid-match.
        "fallback": "espn",
    }


def _roll_date() -> None:
    global _call_date, _calls_today
    today = datetime.now(timezone.utc).date()
    if _call_date != today:
        _call_date = today
        _calls_today = 0


def _can_call() -> bool:
    _roll_date()
    return _calls_today < config.API_FOOTBALL_DAILY_CAP


def _request(path: str, params: dict) -> dict | None:
    """One budgeted GET. Returns parsed JSON or None on any failure/limit."""
    global _calls_today
    if not config.API_FOOTBALL_KEY:
        return None
    if not _can_call():
        print("[live_feed] daily budget reached — skipping call")
        return None
    try:
        r = requests.get(
            f"{config.API_FOOTBALL_BASE}{path}", params=params,
            headers={"x-apisports-key": config.API_FOOTBALL_KEY}, timeout=8)
        _calls_today += 1
        if r.status_code != 200:
            print(f"[live_feed] HTTP {r.status_code} on {path}")
            return None
        return r.json()
    except Exception as exc:
        print(f"[live_feed] request failed: {exc}")
        return None


def _parse_fixture(fix: dict) -> dict:
    """Map one API-Football fixture object to our live-state shape."""
    status = fix["fixture"]["status"]
    short = status.get("short", "")
    elapsed = status.get("elapsed")
    extra = status.get("extra")
    minutes = None
    if elapsed is not None:
        minutes = float(elapsed) + (float(extra) if extra else 0.0)

    # red cards + goal scorers from the events list (already in the payload,
    # so surfacing them costs no extra API call). Red cards are COUNTED per
    # side (two sendings-off are twice the handicap, and the live sim takes
    # counts) — bool(count) keeps every legacy truthiness check working.
    red_home = red_away = 0
    goals_list: list[dict] = []
    home_id = fix["teams"]["home"]["id"]
    for ev in fix.get("events", []) or []:
        etype = ev.get("type")
        team_is_home = (ev.get("team") or {}).get("id") == home_id
        if etype == "Card" and "Red" in (ev.get("detail") or ""):
            if team_is_home:
                red_home += 1
            else:
                red_away += 1
        elif etype == "Goal":
            goals_list.append({
                "team": "home" if team_is_home else "away",
                "player": (ev.get("player") or {}).get("name"),
                "minute": (ev.get("time") or {}).get("elapsed"),
                "detail": ev.get("detail"),  # "Normal Goal" / "Penalty" / etc.
            })

    return {
        "fixture_id": fix["fixture"]["id"],
        "home_name": fix["teams"]["home"]["name"],
        "away_name": fix["teams"]["away"]["name"],
        "home_goals": (fix.get("goals") or {}).get("home") or 0,
        "away_goals": (fix.get("goals") or {}).get("away") or 0,
        "minutes_elapsed": minutes,
        "status_short": short,
        "status_long": status.get("long"),
        "is_live": short in _LIVE_STATUSES,
        "is_finished": short in _FINISHED_STATUSES,
        "red_home": red_home,
        "red_away": red_away,
        "goals_list": goals_list,
    }


def _fetch_live_fixtures() -> list:
    """The raw list of all currently-live fixtures, cached under one shared key
    so every live_state_for() lookup in a poll cycle reuses a SINGLE
    /fixtures?live=all call. Returns [] on no-key / over-budget / error, so
    callers degrade gracefully to 'no live match'."""
    hit = _cache.get(_LIVE_ALL_KEY)
    if hit and (time.time() - hit[0]) < config.API_FOOTBALL_CACHE_SECONDS:
        return hit[1]
    data = _request("/fixtures", {"live": "all"})
    fixtures = data.get("response", []) if data else []
    _cache[_LIVE_ALL_KEY] = (time.time(), fixtures)
    return fixtures


def live_state_for(home: str, away: str) -> dict | None:
    """Find the live World Cup fixture for these two teams and return its
    current state, or None if the feed is unavailable/no match is found.

    Matching is name-based (normalized) against the WC fixtures list; national
    team names are stable across sources, and _norm() absorbs accents/spacing.
    Reads the shared cached fixtures pull, so N lookups in a poll cycle cost
    ONE request (not one per pair).
    """
    if not config.API_FOOTBALL_KEY:
        return _espn_state_for(home, away)

    want = {_norm(home), _norm(away)}
    for fix in _fetch_live_fixtures():
        if fix.get("league", {}).get("id") != config.API_FOOTBALL_LEAGUE_ID:
            continue
        names = {_norm(fix["teams"]["home"]["name"]),
                 _norm(fix["teams"]["away"]["name"])}
        if want == names:
            parsed = _parse_fixture(fix)
            # normalize home/away orientation to OUR schedule's order
            if _norm(parsed["home_name"]) != _norm(home):
                parsed = _flip(parsed)
            return parsed
    # API-Football answered but had nothing for this pair. That is NOT proof
    # the match isn't live: the free plan excludes season-2026 fixtures from
    # its responses (found 2026-07-09, MAR-FRA at 45' with an empty live=all),
    # budget exhaustion returns [] too, and the fallback banner below always
    # promised ESPN on "a feed error". Fall through — _espn_states() is
    # cached like the primary pull, so this costs one keyless call per cycle.
    return _espn_state_for(home, away)


_finished_cache: dict[str, tuple[float, dict | None]] = {}


def finished_state_for(home: str, away: str) -> dict | None:
    """Find a FINISHED World Cup fixture for these two teams and return its
    final state, or None. Unlike live_state_for (which reads /fixtures?live=all
    and only sees in-progress matches), this queries the season fixtures by
    team+season, so it can read a match that finished days ago — which is how
    the bracket resolver reads R16/QF winners after the fact.

    Costs one API call per distinct matchup (cached for the day, since a
    finished result never changes). Used only for bracket resolution, which
    runs rarely, so it's budget-safe.
    """
    if not config.API_FOOTBALL_KEY:
        return None

    cache_key = f"fin|{_norm(home)}|{_norm(away)}"
    hit = _finished_cache.get(cache_key)
    # finished results are immutable — cache for a long time (1 day)
    if hit and (time.time() - hit[0]) < 86400:
        return hit[1]

    # Query the season's fixtures involving one of the teams, then match the
    # pair. API-Football's team search needs an id; simplest robust path is to
    # pull the league+season fixtures and filter locally (one call, cached).
    data = _request("/fixtures", {
        "league": config.API_FOOTBALL_LEAGUE_ID,
        "season": config.API_FOOTBALL_SEASON,
    })
    parsed: dict | None = None
    if data and data.get("response"):
        want = {_norm(home), _norm(away)}
        for fix in data["response"]:
            status = (fix.get("fixture", {}).get("status") or {}).get("short")
            if status not in _FINISHED_STATUSES:
                continue
            names = {_norm(fix["teams"]["home"]["name"]),
                     _norm(fix["teams"]["away"]["name"])}
            if want == names:
                parsed = _parse_fixture(fix)
                if _norm(parsed["home_name"]) != _norm(home):
                    parsed = _flip(parsed)
                break

    if parsed is None:
        parsed = _espn_state_for(home, away, want_finished=True)
    _finished_cache[cache_key] = (time.time(), parsed)
    return parsed


def _flip(state: dict) -> dict:
    """API-Football listed the teams in the opposite home/away order from our
    schedule — swap so the state matches OUR home/away convention."""
    return {
        **state,
        "home_name": state["away_name"], "away_name": state["home_name"],
        "home_goals": state["away_goals"], "away_goals": state["home_goals"],
        "red_home": state["red_away"], "red_away": state["red_home"],
        "goals_list": [
            {**g, "team": "away" if g["team"] == "home" else "home"}
            for g in state.get("goals_list", [])
        ],
    }


# ---------------------------------------------------------------------------
# ESPN fallback (keyless, no practical rate limit) — kicks in whenever the
# budgeted API-Football path can't answer: no key, daily cap exhausted, or a
# feed error. Maps ESPN's public scoreboard into the exact same state shape,
# so every consumer (scoreboard, live panel, freeze logic, bracket resolver)
# works unchanged. API-Football stays primary because its events are richer;
# ESPN means a burned budget can no longer blind the app mid-match.
# ---------------------------------------------------------------------------
ESPN_URL = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
            "fifa.world/scoreboard")
_ESPN_KEY = "__espn__"


def _espn_minute(clock: str) -> float | None:
    m = re.match(r"(\d+)'(?:\s*\+\s*(\d+))?", clock or "")
    if not m:
        return None
    return float(m.group(1)) + (float(m.group(2)) if m.group(2) else 0.0)


def _espn_states() -> list[dict]:
    """All of today's WC fixtures from ESPN, parsed to our state shape."""
    hit = _cache.get(_ESPN_KEY)
    if hit and (time.time() - hit[0]) < config.API_FOOTBALL_CACHE_SECONDS:
        return hit[1]
    out: list[dict] = []
    try:
        r = requests.get(ESPN_URL, timeout=8,
                         headers={"User-Agent": "wc26-suggester/0.3"})
        r.raise_for_status()
        for ev in r.json().get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            if "home" not in sides or "away" not in sides:
                continue
            st = ev.get("status", {})
            state = (st.get("type") or {}).get("state")   # pre | in | post
            period = st.get("period") or 0
            detail = ((st.get("type") or {}).get("detail") or "").lower()
            if state == "in":
                short = ("HT" if "half" in detail and "time" in detail
                         else "ET" if period >= 3 else f"{min(period, 2)}H")
            elif state == "post":
                short = ("PEN" if "pen" in detail
                         else "AET" if period >= 3 else "FT")
            else:
                short = "NS"
            hid = (sides["home"].get("team") or {}).get("id")
            red_home = red_away = 0
            goals_list: list[dict] = []
            for d in comp.get("details", []) or []:
                ttext = ((d.get("type") or {}).get("text") or "")
                is_home = (d.get("team") or {}).get("id") == hid
                if "red card" in ttext.lower():
                    if is_home:
                        red_home += 1
                    else:
                        red_away += 1
                elif d.get("scoringPlay"):
                    ath = d.get("athletesInvolved") or [{}]
                    goals_list.append({
                        "team": "home" if is_home else "away",
                        "player": ath[0].get("displayName"),
                        "minute": (lambda mm: int(mm) if mm else None)(
                            _espn_minute((d.get("clock") or {})
                                         .get("displayValue", ""))),
                        "detail": ttext or None,
                    })
            out.append({
                "fixture_id": ev.get("id"),
                "home_name": (sides["home"].get("team") or {}).get("displayName", ""),
                "away_name": (sides["away"].get("team") or {}).get("displayName", ""),
                "home_goals": int(sides["home"].get("score") or 0),
                "away_goals": int(sides["away"].get("score") or 0),
                "minutes_elapsed": _espn_minute(st.get("displayClock", "")),
                "status_short": short,
                "status_long": (st.get("type") or {}).get("detail"),
                "is_live": state == "in",
                "is_finished": state == "post",
                "red_home": red_home,
                "red_away": red_away,
                "goals_list": goals_list,
                "source": "espn",
            })
    except Exception as exc:
        print(f"[live_feed] espn fallback failed: {exc}")
    _cache[_ESPN_KEY] = (time.time(), out)
    return out


def _espn_state_for(home: str, away: str,
                    want_finished: bool = False) -> dict | None:
    want = {_norm(home), _norm(away)}
    for stt in _espn_states():
        names = {_norm(stt["home_name"]), _norm(stt["away_name"])}
        if want == names and (stt["is_live"] or stt["is_finished"]):
            if want_finished and not stt["is_finished"]:
                continue
            if _norm(stt["home_name"]) != _norm(home):
                stt = _flip(stt)
            return stt
    return None


# ---------------------------------------------------------------------------
# ESPN lineups (Team News) — keyless. ESPN posts matchday rosters ~1h before
# kickoff in the event summary. FACTS ONLY: who starts, who's on the bench,
# and (by absence) who isn't in the squad. Never a model input beyond the
# settled-fact effects applied downstream (an out-of-squad player cannot
# score this match).
# ---------------------------------------------------------------------------
ESPN_SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
                "fifa.world/summary")


def _espn_event_id(home: str, away: str) -> str | None:
    want = {_norm(home), _norm(away)}
    for stt in _espn_states():          # includes pre-match fixtures
        if want == {_norm(stt["home_name"]), _norm(stt["away_name"])}:
            return str(stt["fixture_id"])
    return None


def espn_lineups(home: str, away: str) -> dict:
    """Matchday lineups for a fixture. {available: False} until ESPN posts
    them (~1h pre-kickoff); cached briefly; graceful on any failure."""
    key = f"__lineup__{_norm(home)}|{_norm(away)}"
    hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < 60:
        return hit[1]
    out: dict = {"available": False, "reason": "lineups not posted yet"}
    try:
        ev = _espn_event_id(home, away)
        if ev is None:
            out["reason"] = "fixture not found on ESPN"
        else:
            r = requests.get(ESPN_SUMMARY, params={"event": ev}, timeout=8,
                             headers={"User-Agent": "wc26-suggester/0.3"})
            r.raise_for_status()
            sides: dict = {}
            for side in r.json().get("rosters", []):
                roster = side.get("roster") or []
                if not roster:
                    continue
                team_name = (side.get("team") or {}).get("displayName", "")
                # orient by NAME, not homeAway — ESPN's home/away order can
                # differ from our schedule's (venue-name lesson applies)
                ours = ("home" if _norm(team_name) == _norm(home)
                        else "away" if _norm(team_name) == _norm(away)
                        else None)
                if ours is None:
                    continue
                starters, bench = [], []
                for p in roster:
                    row = {
                        "player": (p.get("athlete") or {}).get("displayName", ""),
                        "shirt": p.get("jersey"),
                        "pos": (p.get("position") or {}).get("abbreviation"),
                    }
                    (starters if p.get("starter") else bench).append(row)
                sides[ours] = {"starters": starters, "bench": bench}
            if sides:
                out = {"available": True, **sides, "source": "espn"}
    except Exception as exc:
        out = {"available": False, "reason": f"lineup fetch failed: {exc}"}
    _cache[key] = (time.time(), out)
    return out
