"""MLS data-layer parsers (canned payloads — no network in tests)."""
from src import mls

_EVENT = {
    "id": "740245", "date": "2026-07-22T23:30Z",
    "status": {"type": {"state": "pre", "shortDetail": "7/22 - 7:30 PM EDT"},
               "displayClock": "0'"},
    "competitions": [{
        "venue": {"fullName": "Chase Stadium"},
        "competitors": [
            {"homeAway": "home", "score": "0",
             "records": [{"summary": "12-3-5"}],
             "team": {"displayName": "Inter Miami CF", "abbreviation": "MIA",
                      "shortDisplayName": "Miami", "logo": "http://x/mia.png"}},
            {"homeAway": "away", "score": "0", "records": [],
             "team": {"displayName": "Chicago Fire FC", "abbreviation": "CHI",
                      "shortDisplayName": "Chicago", "logo": "http://x/chi.png"}},
        ]}],
}

_STANDINGS = {"children": [{
    "name": "Eastern Conference",
    "standings": {"entries": [
        {"team": {"displayName": "Inter Miami CF", "abbreviation": "MIA"},
         "stats": [{"name": "rank", "value": 1}, {"name": "points", "value": 41},
                   {"name": "gamesPlayed", "value": 20}, {"name": "wins", "value": 12},
                   {"name": "losses", "value": 3}, {"name": "ties", "value": 5},
                   {"name": "pointsFor", "value": 40},
                   {"name": "pointsAgainst", "value": 21},
                   {"name": "pointDifferential", "value": 19},
                   {"name": "ppg", "value": 2.05}]},
        {"team": {"displayName": "Chicago Fire FC", "abbreviation": "CHI"},
         "stats": [{"name": "rank", "value": 9}, {"name": "points", "value": 25}]},
    ]}}]}


class TestParsers:
    def test_parse_event(self):
        f = mls.parse_event(_EVENT)
        assert f["home"]["name"] == "Inter Miami CF"
        assert f["home"]["record"] == "12-3-5"
        assert f["away"]["abbrev"] == "CHI"
        assert f["state"] == "pre" and f["venue"] == "Chase Stadium"

    def test_parse_event_tolerates_missing_fields(self):
        f = mls.parse_event({"id": "x"})
        assert f["home"] == {} and f["away"] == {} and f["state"] is None

    def test_parse_standings_orders_by_rank(self):
        out = mls.parse_standings(_STANDINGS)
        assert out[0]["conference"] == "Eastern Conference"
        assert [e["rank"] for e in out[0]["entries"]] == [1, 9]
        assert out[0]["entries"][0]["points"] == 41
        assert out[0]["entries"][0]["goal_diff"] == 19

    def test_parse_game_books_keeps_both_sides(self):
        evs = [{"event_ticker": "KXMLSGAME-26JUL25SJLAG",
                "title": "San Jose vs Los Angeles G"}]
        mkts = {"KXMLSGAME-26JUL25SJLAG": [
            {"ticker": "KXMLSGAME-26JUL25SJLAG-SJ", "yes_sub_title": "San Jose",
             "yes_ask_dollars": "0.5900", "yes_bid_dollars": "0.5600",
             "status": "open"}]}
        out = mls.parse_game_books(evs, mkts)
        row = out[0]["markets"][0]
        assert row["yes_ask"] == "0.5900" and row["yes_bid"] == "0.5600"


class TestEndpoints:
    def test_routes_registered_and_read_only(self):
        from api.main import app
        paths = {r.path for r in app.routes}
        for p in ("/api/mls/scoreboard", "/api/mls/schedule",
                  "/api/mls/standings", "/api/mls/markets"):
            assert p in paths
        for r in app.routes:
            if str(getattr(r, "path", "")).startswith("/api/mls"):
                assert set(r.methods) == {"GET"}   # archive-compatible


_SUMMARY = {
    "header": {"id": "761668", "competitions": [{
        "date": "2026-07-22T23:30Z",
        "status": {"type": {"state": "in", "shortDetail": "38'"},
                   "displayClock": "38'"},
        "competitors": [
            {"homeAway": "home", "score": "1",
             "team": {"id": "183", "displayName": "Columbus Crew",
                      "abbreviation": "CLB"}},
            {"homeAway": "away", "score": "0",
             "team": {"id": "9668", "displayName": "New York City FC",
                      "abbreviation": "NYC"}}]}]},
    "gameInfo": {"venue": {"fullName": "Field"}},
    "boxscore": {"teams": [
        {"team": {"id": "9668"},
         "statistics": [{"name": "possessionPct", "displayValue": "41.0"},
                        {"name": "totalShots", "displayValue": "3"}]},
        {"team": {"id": "183"},
         "statistics": [{"name": "possessionPct", "displayValue": "59.0"},
                        {"name": "totalShots", "displayValue": "8"}]}]},
    "keyEvents": [
        {"clock": {"displayValue": "23'"}, "scoringPlay": True,
         "type": {"text": "Goal"}, "team": {"displayName": "Columbus Crew"},
         "text": "Goal! Header from the corner."}],
}


class TestSummaryParser:
    def test_sides_mapped_by_team_id_not_order(self):
        out = mls.parse_summary(_SUMMARY)
        # boxscore lists AWAY first here; mapping must use team ids
        stat = out["stats"][0]
        assert stat["label"] == "Possession %"
        assert stat["home"] == "59.0" and stat["away"] == "41.0"

    def test_header_and_events(self):
        out = mls.parse_summary(_SUMMARY)
        assert out["home"]["abbrev"] == "CLB" and out["home"]["score"] == "1"
        assert out["state"] == "in" and out["minute"] == "38'"
        ev = out["events"][0]
        assert ev["scoring"] and ev["minute"] == "23'"
        assert ev["team"] == "Columbus Crew"

    def test_tolerates_prematch_empty_boxscore(self):
        out = mls.parse_summary({"header": {}})
        assert out["stats"] == [] and out["events"] == []


class TestBookMatcher:
    _ROW = [{"ticker": "X", "label": "X", "yes_ask": "0.50",
             "yes_bid": "0.48", "status": "active"}]
    _BOOKS = [
        {"event_ticker": "KXMLSGAME-26JUL25SJLAG",
         "title": "San Jose vs Los Angeles G", "markets": _ROW},
        {"event_ticker": "KXMLSGAME-26JUL22SJORL",
         "title": "San Jose vs Orlando", "markets": _ROW},
        {"event_ticker": "KXMLSGAME-26JUL22LAGSTL",
         "title": "Los Angeles G vs Saint Louis", "markets": _ROW},
        {"event_ticker": "KXMLSGAME-26JUL22NYRBCLT",
         "title": "New York RB vs Charlotte", "markets": _ROW},
    ]

    def test_date_disambiguates_double_fixtures(self):
        # San Jose appears Jul 22 AND Jul 25 — the ET date must decide.
        b = mls.find_book("2026-07-22T23:30Z", "San Jose Earthquakes",
                          "Orlando City SC", self._BOOKS)
        assert b["event_ticker"].endswith("26JUL22SJORL")
        b = mls.find_book("2026-07-26T02:30Z", "San Jose Earthquakes",
                          "LA Galaxy", self._BOOKS)     # 02:30Z = Jul 25 ET
        assert b["event_ticker"].endswith("26JUL25SJLAG")

    def test_aliases_bridge_kalshi_names(self):
        b = mls.find_book("2026-07-22T23:30Z", "LA Galaxy",
                          "St. Louis CITY SC", self._BOOKS)
        assert b["event_ticker"].endswith("LAGSTL")
        b = mls.find_book("2026-07-22T23:30Z", "New York Red Bulls",
                          "Charlotte FC", self._BOOKS)
        assert b["event_ticker"].endswith("NYRBCLT")

    def test_no_match_returns_none(self):
        assert mls.find_book("2026-07-22T23:30Z", "Inter Miami CF",
                             "Chicago Fire FC", self._BOOKS) is None


class TestScoutingParsers:
    def test_last_five_and_h2h(self):
        d = {"lastFiveGames": [{"team": {"displayName": "Columbus Crew",
                                         "abbreviation": "CLB"},
                                "events": [{"gameResult": "L", "score": "3-0",
                                            "atVs": "@", "gameDate": "2026-05-10",
                                            "opponent": {"abbreviation": "NYC"}}]}],
             "headToHeadGames": [{"team": {"abbreviation": "CLB"},
                                  "events": [{"gameResult": "W",
                                              "homeTeamScore": "1",
                                              "awayTeamScore": "0",
                                              "atVs": "vs", "gameDate": "2026-05-20",
                                              "opponent": {"abbreviation": "NYC"}}]}]}
        lf = mls._parse_last_five(d)
        assert lf[0]["form"] == "L" and lf[0]["games"][0]["opponent"] == "NYC"
        h = mls._parse_h2h(d)
        assert h[0]["result"] == "W" and h[0]["perspective"] == "CLB"
