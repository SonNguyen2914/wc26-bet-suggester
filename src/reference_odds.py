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
_FIXTURES_ERR_KEY = "ref_fixtures_err"
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


def _provider_error(data) -> str | None:
    """API-Football reports problems as HTTP 200 + an 'errors' field (plan
    restrictions, bad params). Surface that text — a silently empty
    response hides the real cause behind 'fixture not found'."""
    errs = (data or {}).get("errors")
    if not errs:
        return None
    if isinstance(errs, dict):
        return "; ".join(f"{k}: {v}" for k, v in errs.items())
    if isinstance(errs, list):
        return "; ".join(str(e) for e in errs)
    return str(errs)


def _season_fixtures() -> list:
    """All WC fixtures (id + team names), one budgeted call, cached 6h."""
    hit = _cache.get(_FIXTURES_KEY)
    if hit and time.time() - hit[0] < _FIXTURES_TTL:
        return hit[1]
    data = _request("/fixtures", {"league": config.API_FOOTBALL_LEAGUE_ID,
                                  "season": config.API_FOOTBALL_SEASON})
    fixtures = data.get("response", []) if data else []
    err = _provider_error(data)
    if err is None and not fixtures and data is None:
        err = "request failed or budget exhausted"
    _cache[_FIXTURES_ERR_KEY] = err
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


_ESPN_TTL = 10 * 60


def _american_to_decimal(a) -> float | None:
    """'+310' / -115 / 270.0 -> decimal odds. None on junk or zero."""
    try:
        v = float(str(a).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if v == 0:
        return None
    return round(1 + (v / 100 if v > 0 else 100 / abs(v)), 3)


def _espn_reference(match: Match, prediction: dict | None) -> dict | None:
    """Keyless fallback: DraftKings' closing lines via ESPN's summary feed
    (winner + total goals — ESPN carries no correct score). Used when the
    primary odds provider is unavailable (e.g. the API-Football free plan
    refuses season-2026 queries)."""
    key = f"espn|{match.match_id}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _ESPN_TTL:
        d = hit[1]
    else:
        try:
            import requests
            from src.live_feed import ESPN_SUMMARY, _espn_event_id
            ev = _espn_event_id(match.home, match.away)
            if not ev:
                return None
            d = requests.get(ESPN_SUMMARY, params={"event": ev}, timeout=8,
                             headers={"User-Agent": "wc26-bet-suggester"}).json()
            _cache[key] = (time.time(), d)
        except Exception:
            return None

    pc = next((p for p in d.get("pickcenter") or []
               if p.get("homeTeamOdds") or p.get("moneyline")), None)
    if not pc:
        return None
    provider = ((pc.get("provider") or {}).get("name")) or "sportsbook"

    # Winner rows — oriented by TEAM NAME carried in the odds block, never
    # by ESPN's home/away designation (the venue-name lesson).
    rows: list[dict] = []
    for side_odds, espn_side in ((pc.get("homeTeamOdds"), "home"),
                                 (pc.get("awayTeamOdds"), "away")):
        if not side_odds:
            continue
        dec = _american_to_decimal(side_odds.get("moneyLine"))
        if dec is None:
            continue
        name = ((side_odds.get("team") or {}).get("displayName")) or ""
        ours = ("home" if _norm(name) == _norm(match.home)
                else "away" if _norm(name) == _norm(match.away)
                else espn_side)
        row = {"label": match.home if ours == "home" else match.away,
               "odd": dec, "implied": round(1.0 / dec, 4), "books": 1}
        model = _model_lookup("full_time", ours, prediction)
        if model is not None:
            row["model"] = round(model, 4)
        rows.append(row)
    dec = _american_to_decimal((pc.get("drawOdds") or {}).get("moneyLine"))
    if dec is not None:
        row = {"label": "Draw", "odd": dec,
               "implied": round(1.0 / dec, 4), "books": 1}
        model = _model_lookup("full_time", "draw", prediction)
        if model is not None:
            row["model"] = round(model, 4)
        rows.append(row)

    groups: list[dict] = []
    if rows:
        rows.sort(key=lambda r: r["implied"], reverse=True)
        groups.append({"name": "Winner · 90 min", "rows": rows})

    line = pc.get("overUnder")
    trows = []
    for lbl, k in (("Over", "overOdds"), ("Under", "underOdds")):
        dec = _american_to_decimal(pc.get(k))
        if line is not None and dec is not None:
            trows.append({"label": f"{lbl} {line}", "odd": dec,
                          "implied": round(1.0 / dec, 4), "books": 1})
    if trows:
        groups.append({"name": "Total goals", "rows": trows})

    if not groups:
        return None
    return {"match_id": match.match_id,
            "source": f"{provider.lower()} via espn",
            "home_team": match.home, "away_team": match.away,
            "available": True, "bookmaker_count": 1, "groups": groups,
            "disclaimer": (f"Sportsbook reference only — {provider} closing "
                           "lines via ESPN, NOT Kalshi contracts; nothing "
                           "here is buyable through this app. Implied "
                           "probability includes the book's vig. This feed "
                           "has no correct score — exact scorelines appear "
                           "once Kalshi lists that book.")}


def _unavailable(base: dict, reason: str, match: Match,
                 prediction: dict | None) -> dict:
    """Primary provider failed — try the keyless fallback before giving an
    honest 'unavailable', and say why the primary is down either way."""
    fb = _espn_reference(match, prediction)
    if fb:
        fb["note"] = f"primary odds provider unavailable — {reason}"
        return fb
    return {**base, "available": False, "reason": reason}


def reference_odds(match: Match, prediction: dict | None) -> dict:
    """Aggregated pre-match sportsbook odds for one match. Median odd per
    outcome across every quoting bookmaker (robust to one book's outlier),
    with the count of books behind each number."""
    base = {"match_id": match.match_id, "source": "api-football",
            "home_team": match.home, "away_team": match.away}
    if not config.API_FOOTBALL_KEY:
        return _unavailable(base, "no API key configured", match, prediction)

    hit = _cache.get(match.match_id)
    if hit and time.time() - hit[0] < _ODDS_TTL:
        payload = hit[1]
    else:
        fid = _fixture_id(match.home, match.away)
        if fid is None:
            reason = "fixture not found at the odds provider"
            err = _cache.get(_FIXTURES_ERR_KEY)
            if err:
                reason += f" — provider says: {err}"
            return _unavailable(base, reason, match, prediction)
        payload = _request("/odds", {"fixture": fid})
        if payload is not None:
            _cache[match.match_id] = (time.time(), payload)

    entries = (payload or {}).get("response", [])
    if not entries:
        reason = ("odds request failed or budget exhausted"
                  if payload is None
                  else "provider lists no pre-match odds yet")
        err = _provider_error(payload)
        if err:
            reason += f" — provider says: {err}"
        return _unavailable(base, reason, match, prediction)

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
        return _unavailable(base,
                            "provider odds carry none of the tracked bet types",
                            match, prediction)
    return {**base, "available": True, "bookmaker_count": len(book_names),
            "groups": out_groups,
            "disclaimer": ("Sportsbook reference only — these are NOT Kalshi "
                           "contracts and cannot be bought through this app. "
                           "Median decimal odd across quoting books; implied "
                           "probability includes the bookmakers' vig.")}
