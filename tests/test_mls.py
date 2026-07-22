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
