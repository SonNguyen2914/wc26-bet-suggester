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

import time
import unicodedata
from datetime import date, datetime, timezone

import requests

import config

# --- daily budget counter (process-local; resets on UTC date change) -------
_call_date: date | None = None
_calls_today = 0

# --- tiny response cache: fixture_key -> (fetched_at, parsed_state) ---------
_cache: dict[str, tuple[float, dict | None]] = {}

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
    # so surfacing them costs no extra API call).
    red_home = red_away = False
    goals_list: list[dict] = []
    home_id = fix["teams"]["home"]["id"]
    for ev in fix.get("events", []) or []:
        etype = ev.get("type")
        team_is_home = (ev.get("team") or {}).get("id") == home_id
        if etype == "Card" and "Red" in (ev.get("detail") or ""):
            if team_is_home:
                red_home = True
            else:
                red_away = True
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


def live_state_for(home: str, away: str) -> dict | None:
    """Find the live World Cup fixture for these two teams and return its
    current state, or None if the feed is unavailable/no match is found.

    Matching is name-based (normalized) against the WC fixtures list; national
    team names are stable across sources, and _norm() absorbs accents/spacing.
    Cached briefly so repeated reads cost one request.
    """
    if not config.API_FOOTBALL_KEY:
        return None

    cache_key = f"{_norm(home)}|{_norm(away)}"
    hit = _cache.get(cache_key)
    if hit and (time.time() - hit[0]) < config.API_FOOTBALL_CACHE_SECONDS:
        return hit[1]

    # Pull all live fixtures (cheap: one call covers every live match at once).
    data = _request("/fixtures", {"live": "all"})
    parsed: dict | None = None
    if data and data.get("response"):
        want = {_norm(home), _norm(away)}
        for fix in data["response"]:
            if fix.get("league", {}).get("id") != config.API_FOOTBALL_LEAGUE_ID:
                continue
            names = {_norm(fix["teams"]["home"]["name"]),
                     _norm(fix["teams"]["away"]["name"])}
            if want == names:
                parsed = _parse_fixture(fix)
                # normalize home/away orientation to OUR schedule's order
                if _norm(parsed["home_name"]) != _norm(home):
                    parsed = _flip(parsed)
                break

    _cache[cache_key] = (time.time(), parsed)
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
