"""Sportsbook reference odds — DISPLAY-ONLY (API-Football /odds).

Kalshi opens some books (notably Correct Score) only 1-2 days before
kickoff. Until then the match page can still show what the wider betting
market thinks, via API-Football's pre-match odds aggregation (Bet365,
Pinnacle, Bwin, ...). Ground rules, per the project's honesty principle:

- These contracts are NOT buyable on Kalshi. They never enter the
  suggestion board, the strategy engine, or any edge gate — a dedicated
  endpoint serves them and the UI labels them "reference".
- Prices carry the bookmakers' vig (implied = 1/odd, un-normalised); the
  model column beside them is attached only where the cached simulation
  states that exact number (W/D/L, exact scores) — never derived loosely.
- Budget: reuses live_feed's counted _request(). One call fills the
  season fixture-id map (cached 6h); one call per match fetches its odds
  (cached 30 min). Worst case a handful of calls a day.
"""
from __future__ import annotations

import time
from statistics import median

import config
from src.live_feed import _norm, _request
from src.schedule_data import Match

_FIXTURES_KEY = "ref_fixtures"
_FIXTURES_TTL = 6 * 3600
_ODDS_TTL = 30 * 60
_cache: dict = {}

# API-Football bet names -> display group + model join strategy.
# Order here is display order. Anything not listed is ignored.
_BET_GROUPS: list[tuple[str, str, str]] = [
    # (api bet name, display name, model_key strategy)
    ("Match Winner", "Winner · 90 min", "full_time"),
    ("Exact Score", "Exact score · 90 min", "scoreline"),
    ("Correct Score", "Exact score · 90 min", "scoreline"),
    ("Goals Over/Under", "Total goals", "none"),
    ("Both Teams Score", "Both teams to score", "none"),
    ("Double Chance", "Double chance", "none"),
    ("Team To Score First", "First team to score", "none"),
]
_BET_NAME_TO_GROUP = {api: (disp, strat) for api, disp, strat in _BET_GROUPS}
_GROUP_ORDER = list(dict.fromkeys(disp for _, disp, _ in _BET_GROUPS))


def _season_fixtures() -> list:
    """All WC fixtures (id + team names), one budgeted call, cached 6h."""
    hit = _cache.get(_FIXTURES_KEY)
    if hit and time.time() - hit[0] < _FIXTURES_TTL:
        return hit[1]
    data = _request("/fixtures", {"league": config.API_FOOTBALL_LEAGUE_ID,
                                  "season": config.API_FOOTBALL_SEASON})
    fixtures = data.get("response", []) if data else []
    if fixtures:                      # never cache a failed/empty fetch
        _cache[_FIXTURES_KEY] = (time.time(), fixtures)
    return fixtures


def _fixture_id(home: str, away: str) -> int | None:
    want = {_norm(home), _norm(away)}
    for fix in _season_fixtures():
        names = {_norm(fix["teams"]["home"]["name"]),
                 _norm(fix["teams"]["away"]["name"])}
        if names == want:
            return fix["fixture"]["id"]
    return None


def _model_lookup(strategy: str, label: str, prediction: dict | None):
    """The model's own number for a reference row — exact joins only.
    W/D/L reads the simulation summary; exact scores read the scoreline
    distribution. Anything else honestly returns None (no loose deriving
    from a truncated top-30 scoreline list)."""
    if not prediction:
        return None
    if strategy == "full_time":
        ft = (prediction.get("summary") or {}).get("full_time") or {}
        return {"home": ft.get("home_win"), "draw": ft.get("draw"),
                "away": ft.get("away_win")}.get(label)
    if strategy == "scoreline":
        for s in prediction.get("scorelines") or []:
            if s.get("score") == label:
                return s.get("prob")
    return None


def _row_label(bet_name: str, value: str, match: Match) -> tuple[str, str]:
    """(display label, model-join label). Winner rows name the team;
    exact scores normalise '1:0' -> '1-0' (home-away, our convention)."""
    v = (value or "").strip()
    if bet_name in ("Match Winner",):
        side = {"Home": "home", "Draw": "draw", "Away": "away"}.get(v)
        disp = {"home": match.home, "draw": "Draw",
                "away": match.away}.get(side or "", v)
        return disp, (side or v)
    if bet_name in ("Exact Score", "Correct Score"):
        score = v.replace(":", "-")
        return score, score
    if bet_name == "Double Chance":
        return (v.replace("Home", match.home).replace("Away", match.away), v)
    if bet_name == "Team To Score First":
        return ({"Home": match.home, "Away": match.away}.get(v, v), v)
    return v, v


def reference_odds(match: Match, prediction: dict | None) -> dict:
    """Aggregated pre-match sportsbook odds for one match. Median odd per
    outcome across every quoting bookmaker (robust to one book's outlier),
    with the count of books behind each number."""
    base = {"match_id": match.match_id, "source": "api-football",
            "home_team": match.home, "away_team": match.away}
    if not config.API_FOOTBALL_KEY:
        return {**base, "available": False, "reason": "no API key configured"}

    hit = _cache.get(match.match_id)
    if hit and time.time() - hit[0] < _ODDS_TTL:
        payload = hit[1]
    else:
        fid = _fixture_id(match.home, match.away)
        if fid is None:
            return {**base, "available": False,
                    "reason": "fixture not found at the odds provider"}
        payload = _request("/odds", {"fixture": fid})
        if payload is not None:
            _cache[match.match_id] = (time.time(), payload)

    entries = (payload or {}).get("response", [])
    if not entries:
        return {**base, "available": False,
                "reason": "provider lists no pre-match odds yet"}

    # odds[(group, label)] -> list of decimal odds across bookmakers
    collected: dict[tuple[str, str, str], list[float]] = {}
    book_names: set[str] = set()
    for entry in entries:
        for bk in entry.get("bookmakers", []) or []:
            for bet in bk.get("bets", []) or []:
                grp = _BET_NAME_TO_GROUP.get(bet.get("name") or "")
                if not grp:
                    continue
                disp_group, strategy = grp
                for val in bet.get("values", []) or []:
                    try:
                        odd = float(val.get("odd"))
                    except (TypeError, ValueError):
                        continue
                    if odd <= 1.0:
                        continue
                    label, mlabel = _row_label(bet.get("name"),
                                               str(val.get("value")), match)
                    key = (disp_group, label, mlabel)
                    collected.setdefault(key, []).append(odd)
                    book_names.add(bk.get("name") or "?")

    groups: dict[str, list[dict]] = {}
    for (disp_group, label, mlabel), odds in collected.items():
        odd = round(median(odds), 2)
        row = {"label": label, "odd": odd,
               "implied": round(1.0 / odd, 4), "books": len(odds)}
        strategy = next((s for a, d, s in _BET_GROUPS if d == disp_group), "none")
        model = _model_lookup(strategy, mlabel, prediction)
        if model is not None:
            row["model"] = round(model, 4)
        groups.setdefault(disp_group, []).append(row)

    out_groups = []
    for name in _GROUP_ORDER:
        rows = groups.get(name)
        if not rows:
            continue
        rows.sort(key=lambda r: r["implied"], reverse=True)
        out_groups.append({"name": name, "rows": rows})

    if not out_groups:
        return {**base, "available": False,
                "reason": "provider odds carry none of the tracked bet types"}
    return {**base, "available": True, "bookmaker_count": len(book_names),
            "groups": out_groups,
            "disclaimer": ("Sportsbook reference only — these are NOT Kalshi "
                           "contracts and cannot be bought through this app. "
                           "Median decimal odd across quoting books; implied "
                           "probability includes the bookmakers' vig.")}
