"""Live-read phases (ET/pens), red-card counts, MOV ET/PEN pricing, and
buy-side (ask) market pricing."""
import numpy as np

from src.kalshi_client import _classify_outcome, _market_yes_price
from src.models.simulator import MatchSimulator, PENALTY_HOME_WIN_P
from tests.test_score_classification import _match, _mkt

HOME = {"attack": 1.2, "defence": 0.8, "form": 0.7, "set_piece_threat": 0.2,
        "red_card_risk": 0.05, "fatigue": 0.2, "elo": 1800}
AWAY = {"attack": 1.0, "defence": 0.9, "form": 0.6, "set_piece_threat": 0.2,
        "red_card_risk": 0.05, "fatigue": 0.2, "elo": 1750}


class TestAskPricing:
    def test_thin_book_prices_at_ask_not_mid(self):
        # bid 1c / ask 2c: mid 1.5c is untradeable -> price the buyable 2c
        m = {"yes_bid_dollars": "0.01", "yes_ask_dollars": "0.02"}
        assert _market_yes_price(m) == 0.02

    def test_no_side_derivation_uses_no_bid(self):
        # selling NO at its 97c bid == buying YES at 3c
        m = {"no_bid_dollars": "0.97", "no_ask_dollars": "0.99"}
        assert _market_yes_price(m) == 0.03

    def test_bid_only_book_is_unpriceable(self):
        # a bid with no ask means nobody is selling — you can't buy, so the
        # market is honestly skipped rather than priced off one side
        m = {"yes_bid_dollars": "0.40", "yes_ask_dollars": None}
        assert _market_yes_price(m) is None


class TestMovEtPenClassification:
    def test_reg_et_pen_suffixes(self):
        m = _match("Morocco", "France")
        ev = "KXWCMOV-26JUL09FRAMAR"
        assert _classify_outcome(m, _mkt("KXWCMOV-26JUL09FRAMAR-FRAREG"), ev) == "away_win"
        assert _classify_outcome(m, _mkt("KXWCMOV-26JUL09FRAMAR-FRAET"), ev) == "away_win_et"
        assert _classify_outcome(m, _mkt("KXWCMOV-26JUL09FRAMAR-MARPEN"), ev) == "home_win_pens"


class TestAdvanceBreakdown:
    def test_mov_components_sum_to_advance(self):
        sim = MatchSimulator(n_simulations=40000, seed=5)
        r = sim.simulate(HOME, AWAY, stage="knockout")
        adv = r["advance"]
        # home advance = wins in 90 + wins in ET + wins on pens
        total = r["outcomes"]["home_win"] + adv["home_win_et"] + adv["home_win_pens"]
        assert abs(total - adv["home"]) < 0.01
        # and the outcome-key mapping reaches them
        assert sim.prob_for_outcome_key(r, "home_win_et") == adv["home_win_et"]
        assert sim.prob_for_outcome_key(r, "away_win_pens") == adv["away_win_pens"]


class TestPhases:
    def test_et_phase_prices_only_advancement(self):
        sim = MatchSimulator(n_simulations=20000, seed=5)
        r = sim.simulate_remaining(HOME, AWAY, 1, 1, 100, stage="knockout",
                                   phase="et")
        assert r["outcomes"] == {"home_win": 0.0, "draw": 1.0, "away_win": 0.0}
        assert r["props"] == {} and r["scorelines"] == []
        assert r["live_state"]["phase"] == "et"
        adv = r["advance"]
        assert 0 < adv["home"] < 1 and abs(adv["home"] + adv["away"] - 1) < 1e-6
        # stronger side should be favoured in the remaining ET
        assert adv["home"] > 0.5

    def test_et_leader_heavily_favoured_late(self):
        sim = MatchSimulator(n_simulations=20000, seed=5)
        r = sim.simulate_remaining(HOME, AWAY, 2, 1, 118, stage="knockout",
                                   phase="et")
        assert r["advance"]["home"] > 0.9

    def test_auto_infers_et_past_90(self):
        sim = MatchSimulator(n_simulations=5000, seed=5)
        r = sim.simulate_remaining(HOME, AWAY, 0, 0, 95, stage="knockout")
        assert r["live_state"]["phase"] == "et"

    def test_pens_phase_is_coinflip(self):
        sim = MatchSimulator(n_simulations=1000, seed=5)
        r = sim.simulate_remaining(HOME, AWAY, 2, 2, 120, stage="knockout",
                                   phase="pens")
        assert r["advance"]["home"] == PENALTY_HOME_WIN_P
        assert r["advance"]["method"] == "penalty_coinflip"


class TestRedCardCounts:
    def test_second_red_compounds(self):
        sim1 = MatchSimulator(n_simulations=40000, seed=5)
        one = sim1.simulate_remaining(HOME, AWAY, 0, 0, 60, red_home=1)
        sim2 = MatchSimulator(n_simulations=40000, seed=5)
        two = sim2.simulate_remaining(HOME, AWAY, 0, 0, 60, red_home=2)
        # two reds hurt strictly more than one
        assert two["outcomes"]["home_win"] < one["outcomes"]["home_win"]
        assert two["live_state"]["red_home"] == 2

    def test_bools_still_work(self):
        sim = MatchSimulator(n_simulations=5000, seed=5)
        r = sim.simulate_remaining(HOME, AWAY, 0, 0, 60, red_home=True)
        assert r["live_state"]["red_home"] == 1


class TestContinuationMarketFilter:
    def test_et_phase_prices_only_advancement_markets(self):
        """In ET/pens the 90-min books are settled facts; price_live must not
        blend them with stale prices ('draw after 90: 70%' nonsense)."""
        from src.suggester import SuggesterEngine
        from src.schedule_data import get_match
        eng = SuggesterEngine()
        m = get_match("MAR_FRA")
        out = eng.price_live(m, 1, 1, 100, phase="et")
        keys = {r["outcome_key"] for r in out["markets"]}
        allowed = {"home_advance", "away_advance", "home_win_et",
                   "away_win_et", "home_win_pens", "away_win_pens"}
        assert keys <= allowed, f"settled 90-min markets leaked: {keys - allowed}"
        # regulation phase keeps the full table
        out_reg = eng.price_live(m, 1, 1, 60, phase="regulation")
        assert any((r["outcome_key"] or "").startswith("over_")
                   for r in out_reg["markets"])


class TestAuditFixes:
    def test_two_reds_counted_from_feed(self, monkeypatch):
        import config
        import src.live_feed as lf
        from tests.test_live_feed import _fixture, _patch
        events = [
            {"type": "Card", "detail": "Red Card", "team": {"id": 100}},
            {"type": "Card", "detail": "Red Card", "team": {"id": 100}},
            {"type": "Card", "detail": "Red Card", "team": {"id": 200}},
        ]
        _patch(monkeypatch, [_fixture("Brazil", "Norway", 0, 0, 60,
                                      events=events)])
        s = lf.live_state_for("Brazil", "Norway")
        assert s["red_home"] == 2 and s["red_away"] == 1

    def test_group_match_rejects_et_phase(self):
        from fastapi.testclient import TestClient
        import src.schedule_data as sd
        from api.main import app
        m = sd.load_schedule()[0]
        orig = m.stage
        m.stage = "group"           # force a group match temporarily
        try:
            client = TestClient(app)
            r = client.post(f"/api/prediction/{m.match_id}/live",
                            json={"minutes_elapsed": 100, "phase": "et"})
            assert r.status_code == 422
        finally:
            m.stage = orig

    def test_bracket_probs_carry_edges_from_cache(self):
        from src.bracket import _win_probs
        import src.bracket as br
        class M:  # minimal resolved-match stand-in
            fully_resolved = True
            match_id = "TST_EDGE"
            home, away = "Morocco", "France"
        import src.cache as cache
        orig = cache.latest_for_match
        cache.latest_for_match = lambda mid: {
            "markets": [
                {"outcome_key": "home_win", "model_probability": 0.28, "edge": 0.07},
                {"outcome_key": "draw", "model_probability": 0.25, "edge": 0.01},
                {"outcome_key": "away_win", "model_probability": 0.52, "edge": -0.08},
            ]}
        try:
            p = _win_probs(M())
            assert p["home_edge"] == 0.07 and p["away_edge"] == -0.08
        finally:
            cache.latest_for_match = orig


class TestPlayerProps:
    def test_thinning_math_and_sanity(self):
        from src.player_props import props_for
        pp = props_for("Argentina", "Switzerland", "knockout", 1.85, 1.63)
        h = pp["home"]
        assert h, "Argentina roster missing from player_rates.json"
        # Messi is Argentina's top share and therefore top anytime prob
        assert h[0]["player"].upper().endswith("MESSI")
        assert h[0]["anytime"] > h[-1]["anytime"] > 0
        assert all(0 < p["anytime"] < 1 for p in h)
        # first-goal probabilities + P(no goal) must not exceed 1 in total
        # (they'd sum to exactly 1 with the FULL roster; top-N is a subset)
        tot_first = sum(p["first_goal"] for p in pp["home"] + pp["away"])
        assert tot_first + pp["p_no_goal"] <= 1.0 + 1e-6
        # damping applied: knockout lambdas below the raw xg inputs
        assert pp["lambda"]["home"] < 1.85

    def test_all_eight_teams_have_rates(self):
        from src.player_props import team_players
        for t in ("France", "Morocco", "Spain", "Belgium",
                  "Norway", "England", "Argentina", "Switzerland"):
            assert len(team_players(t)) >= 8, t


class TestTournamentModelGuard:
    def test_unknown_team_returns_none_not_crash(self):
        """Regression: once the QFs finish, SF teams aren't in the static
        _QF_PAIRS table — the endpoint must degrade, not 500."""
        from src.player_props import tournament_anytime
        assert tournament_anytime("Future SF Winner", 0.4) is None

    def test_join_markets_survives_missing_tournament_model(self):
        from src.player_props import join_markets
        players = [{"player": "Ghost", "shirt": 99, "share": 0.3, "goals": 0,
                    "attempts": 3, "matches": 5, "starts": 5,
                    "anytime": 0.2, "first_goal": 0.1}]
        # team outside the bracket: must not raise
        join_markets("Future SF Winner", players)
        assert players[0]["tournament_anytime"] is None


class TestLiquidityGate:
    def test_dead_book_is_not_a_market(self):
        from src.player_props import _is_tradeable
        assert not _is_tradeable(0.95, 0.05)   # the Upamecano case
        assert not _is_tradeable(0.95, None)
        assert _is_tradeable(0.08, 0.04)       # Tielemans 1+ style — real
        assert _is_tradeable(0.02, 0.01)       # cheap but two-sided
        assert not _is_tradeable(0.30, 0.05)   # 25c spread — no market


class TestForecastAndLineups:
    def test_slot_dist_sums_to_one_all_rounds(self):
        from src.bracket import _slot_dist
        for slot in ("SF1", "SF2", "FINAL"):
            d = _slot_dist(slot)
            assert abs(sum(d.values()) - 1.0) < 1e-6, (slot, d)
        assert max(_slot_dist("FINAL"), key=lambda t: _slot_dist("FINAL")[t])

    def test_lineup_squad_facts(self):
        from src.player_props import apply_lineups
        props = {"home": [
            {"player": "Kylian Mbappe", "anytime": 0.47, "p2": 0.1,
             "p3": 0.02, "first_goal": 0.2},
            {"player": "Ghost Man", "anytime": 0.10, "p2": 0.01,
             "p3": 0.0, "first_goal": 0.03},
        ], "away": []}
        lineups = {"available": True, "home": {
            "starters": [{"player": "Kylian Mbappe"}], "bench": []}}
        apply_lineups(props, lineups)
        assert props["home"][0]["squad"] == "starter"
        assert props["home"][0]["anytime"] == 0.47      # untouched
        assert props["home"][1]["squad"] == "out"
        assert props["home"][1]["anytime"] == 0.0       # settled fact

    def test_et_fatigue_rule(self):
        from src.db import MatchResult, SessionLocal, init_db, utcnow
        from src.schedule_data import effective_team_stats, get_team_stats
        init_db()
        with SessionLocal() as s:
            s.query(MatchResult).filter(MatchResult.match_id == "TST_ET").delete()
            s.add(MatchResult(match_id="TST_ET", home="Norway", away="England",
                              home_goals=1, away_goals=1, status_short="PEN",
                              goals_json="[]", finished_at=utcnow()))
            s.commit()
        try:
            assert get_team_stats("Norway")["fatigue"] < 0.30   # hand value
            assert effective_team_stats("Norway")["fatigue"] >= 0.30
        finally:
            with SessionLocal() as s:
                s.query(MatchResult).filter(MatchResult.match_id == "TST_ET").delete()
                s.commit()


class TestFirstGoalMarkets:
    """KXWCFIRSTGOAL — per-player First Goalscorer, priced against the
    model's Poisson first-goal race. Discovered live 2026-07-09."""

    def _props(self):
        return {"home": [{"player": "Erling Haaland", "shirt": 9,
                          "first_goal": 0.18}],
                "away": [{"player": "Harry Kane", "shirt": 9,
                          "first_goal": 0.15}]}

    def test_join_prices_players_skips_nogoal_and_dead_books(self, monkeypatch):
        import config
        import src.player_props as pp

        def fake(series, home, away):
            if series != "KXWCFIRSTGOAL":
                return []
            return [
                # real two-sided book on Kane (away = ENG)
                {"ticker": "KXWCFIRSTGOAL-26JUL11NORENG-ENGHKANE9",
                 "yes_ask_dollars": "0.20", "yes_bid_dollars": "0.15"},
                # the NOGOAL leg — no shirt digits, must never attach
                {"ticker": "KXWCFIRSTGOAL-26JUL11NORENG-NOGOAL",
                 "yes_ask_dollars": "0.10", "yes_bid_dollars": "0.08"},
                # dead book on Haaland — placeholder 95c ask, 2c bid
                {"ticker": "KXWCFIRSTGOAL-26JUL11NORENG-NORAHAALA9",
                 "yes_ask_dollars": "0.95", "yes_bid_dollars": "0.02"},
            ]

        monkeypatch.setattr(pp, "_match_event_markets", fake)
        props = self._props()
        pp.join_match_markets("Norway", "England", props)

        kane = props["away"][0]["first_goal_market"]
        assert kane["implied"] == 0.20
        assert kane["multiplier"] == 5.0
        anchored = config.MODEL_WEIGHT * 0.15 + (1 - config.MODEL_WEIGHT) * 0.20
        assert abs(kane["likelihood"] - round(anchored, 4)) < 1e-9
        assert abs(kane["edge"] - round(anchored - 0.20, 4)) < 1e-9
        # dead book: model stands alone, no fictional price attached
        assert "first_goal_market" not in props["home"][0]

    def test_cheapest_ask_wins_on_duplicates(self, monkeypatch):
        import src.player_props as pp

        def fake(series, home, away):
            if series != "KXWCFIRSTGOAL":
                return []
            return [
                {"ticker": "KXWCFIRSTGOAL-26JUL11NORENG-ENGHKANE9",
                 "yes_ask_dollars": "0.22", "yes_bid_dollars": "0.17"},
                {"ticker": "KXWCFIRSTGOAL-26JUL11NORENG-ENGHKANE9",
                 "yes_ask_dollars": "0.19", "yes_bid_dollars": "0.15"},
            ]

        monkeypatch.setattr(pp, "_match_event_markets", fake)
        props = self._props()
        pp.join_match_markets("Norway", "England", props)
        assert props["away"][0]["first_goal_market"]["implied"] == 0.19

    def test_firstgoal_family_never_reaches_match_pipeline(self):
        from src.kalshi_client import SKIP_FAMILIES, _classify_outcome
        from src.schedule_data import get_match
        assert "KXWCFIRSTGOAL" in SKIP_FAMILIES
        assert "KXWCPREPACK" in SKIP_FAMILIES
        m = get_match("NOR_ENG")
        mk = {"ticker": "KXWCFIRSTGOAL-26JUL11NORENG-ENGHKANE9",
              "title": "", "yes_sub_title": "Harry Kane"}
        assert _classify_outcome(m, mk, "KXWCFIRSTGOAL-26JUL11NORENG") is None


class TestReferenceOdds:
    """Sportsbook reference layer (API-Football /odds) — display-only.
    Canned payloads; no network. The one rule that matters most: these
    numbers must be impossible to confuse with buyable Kalshi edge."""

    def _match(self):
        from src.schedule_data import get_match
        return get_match("NOR_ENG")

    def _payload(self):
        return {"response": [{"bookmakers": [
            {"name": "BookA", "bets": [
                {"name": "Match Winner", "values": [
                    {"value": "Home", "odd": "5.50"},
                    {"value": "Draw", "odd": "4.20"},
                    {"value": "Away", "odd": "1.60"}]},
                {"name": "Exact Score", "values": [
                    {"value": "0:1", "odd": "7.00"},
                    {"value": "1:1", "odd": "6.50"}]},
                {"name": "Man of the Match", "values": [   # not tracked
                    {"value": "H. Kane", "odd": "4.00"}]},
            ]},
            {"name": "BookB", "bets": [
                {"name": "Match Winner", "values": [
                    {"value": "Home", "odd": "6.00"},
                    {"value": "Draw", "odd": "4.00"},
                    {"value": "Away", "odd": "1.66"}]},
                {"name": "Correct Score", "values": [
                    {"value": "0:1", "odd": "7.50"}]},
            ]},
        ]}]}

    def test_groups_median_and_model_join(self, monkeypatch):
        import src.reference_odds as ro
        monkeypatch.setattr(ro, "_fixture_id", lambda h, a: 123)
        monkeypatch.setattr(ro, "_request", lambda p, q: self._payload())
        ro._cache.clear()
        pred = {"summary": {"full_time": {"home_win": 0.14, "draw": 0.24,
                                          "away_win": 0.62}},
                "scorelines": [{"score": "0-1", "prob": 0.11}]}
        out = ro.reference_odds(self._match(), pred)
        assert out["available"] and out["bookmaker_count"] == 2
        names = [g["name"] for g in out["groups"]]
        assert names == ["Winner · 90 min", "Exact score · 90 min"]
        winner = {r["label"]: r for r in out["groups"][0]["rows"]}
        # median of 5.50/6.00 = 5.75; team names resolved from OUR match
        assert winner["Norway"]["odd"] == 5.75 and winner["Norway"]["books"] == 2
        assert winner["England"]["model"] == 0.62
        score = {r["label"]: r for r in out["groups"][1]["rows"]}
        # '0:1' normalised to our home-away '0-1'; model joined exactly;
        # Exact Score + Correct Score are the same display group
        assert score["0-1"]["books"] == 2 and score["0-1"]["model"] == 0.11
        assert "1-1" in score and "model" not in score["1-1"]
        # untracked bet types (Man of the Match) never leak through
        assert all(g["name"] in ("Winner · 90 min", "Exact score · 90 min")
                   for g in out["groups"])
        assert "NOT Kalshi" in out["disclaimer"]

    def test_degrades_without_provider(self, monkeypatch):
        # ESPN fallback isolated to None: this tests the PRIMARY path's
        # honest degradation (the fallback has its own offline tests) —
        # without the isolation these tests would hit the real network.
        import src.reference_odds as ro
        monkeypatch.setattr(ro, "_fixture_id", lambda h, a: 123)
        monkeypatch.setattr(ro, "_request", lambda p, q: None)
        monkeypatch.setattr(ro, "_espn_reference", lambda m, p: None)
        ro._cache.clear()
        out = ro.reference_odds(self._match(), None)
        assert out["available"] is False and "reason" in out

    def test_unknown_fixture_degrades(self, monkeypatch):
        import src.reference_odds as ro
        monkeypatch.setattr(ro, "_fixture_id", lambda h, a: None)
        monkeypatch.setattr(ro, "_espn_reference", lambda m, p: None)
        ro._cache.clear()
        out = ro.reference_odds(self._match(), None)
        assert out["available"] is False and "fixture" in out["reason"]


class TestEspnFallbackOdds:
    """Keyless DraftKings-via-ESPN fallback for the reference layer."""

    def _summary(self, espn_home_is_england: bool):
        # ESPN block with the odds carrying team names — the parser must
        # orient by NAME even when ESPN's home/away disagrees with ours.
        h_name = "England" if espn_home_is_england else "Norway"
        a_name = "Norway" if espn_home_is_england else "England"
        return {"pickcenter": [{
            "provider": {"name": "DraftKings"},
            "homeTeamOdds": {"moneyLine": -115 if espn_home_is_england else 310,
                             "team": {"displayName": h_name}},
            "awayTeamOdds": {"moneyLine": 310 if espn_home_is_england else -115,
                             "team": {"displayName": a_name}},
            "drawOdds": {"moneyLine": 270.0},
            "overUnder": 2.5, "overOdds": -135.0, "underOdds": 110.0,
        }]}

    def _run(self, summary):
        import time as _t

        import src.reference_odds as ro
        from src.schedule_data import get_match
        m = get_match("NOR_ENG")
        ro._cache.clear()
        ro._cache[f"espn|{m.match_id}"] = (_t.time(), summary)
        pred = {"summary": {"full_time": {"home_win": 0.18, "draw": 0.26,
                                          "away_win": 0.56}}}
        return ro._espn_reference(m, pred)

    def test_american_conversion(self):
        from src.reference_odds import _american_to_decimal
        assert _american_to_decimal("+310") == 4.1
        assert _american_to_decimal(-115) == 1.87
        assert _american_to_decimal(270.0) == 3.7
        assert _american_to_decimal("junk") is None
        assert _american_to_decimal(0) is None

    def test_orientation_and_groups(self):
        out = self._run(self._summary(espn_home_is_england=False))
        assert out["available"] and out["source"] == "draftkings via espn"
        winner = {r["label"]: r for r in out["groups"][0]["rows"]}
        assert winner["Norway"]["odd"] == 4.1        # +310 underdog
        assert winner["England"]["odd"] == 1.87      # -115 favourite
        assert winner["Norway"]["model"] == 0.18     # OUR home = Norway
        assert winner["Draw"]["model"] == 0.26
        totals = {r["label"] for r in out["groups"][1]["rows"]}
        assert totals == {"Over 2.5", "Under 2.5"}

    def test_orientation_survives_flipped_espn_sides(self):
        # ESPN home = England (disagrees with our NOR_ENG orientation):
        # England must STILL get the -115 favourite line and Norway's model
        # number must stay 0.18 — names win, positions lie.
        out = self._run(self._summary(espn_home_is_england=True))
        winner = {r["label"]: r for r in out["groups"][0]["rows"]}
        assert winner["England"]["odd"] == 1.87
        assert winner["Norway"]["odd"] == 4.1
        assert winner["Norway"]["model"] == 0.18

    def test_fallback_wired_into_unavailable_paths(self, monkeypatch):
        import src.reference_odds as ro
        from src.schedule_data import get_match
        m = get_match("NOR_ENG")
        monkeypatch.setattr(ro, "_espn_reference",
                            lambda match, pred: {"available": True,
                                                 "groups": [{"name": "x",
                                                             "rows": []}]})
        out = ro._unavailable({"match_id": m.match_id}, "plan blocked", m, None)
        assert out["available"] and "plan blocked" in out["note"]
        monkeypatch.setattr(ro, "_espn_reference", lambda match, pred: None)
        out = ro._unavailable({"match_id": m.match_id}, "plan blocked", m, None)
        assert out["available"] is False and out["reason"] == "plan blocked"


class TestLiveFallthroughToEspn:
    def test_empty_primary_feed_still_finds_the_match_via_espn(self, monkeypatch):
        """Regression: MAR-FRA at 45' with live=[] on the site. With a key
        configured, an empty API-Football pull (free plan excludes season
        2026; budget exhaustion looks identical) must fall through to ESPN
        instead of returning None."""
        import config
        import src.live_feed as lf
        monkeypatch.setattr(config, "API_FOOTBALL_KEY", "some-key")
        monkeypatch.setattr(lf, "_fetch_live_fixtures", lambda: [])
        sentinel = {"home_name": "Morocco", "away_name": "France",
                    "is_live": True, "status_short": "1H"}
        monkeypatch.setattr(lf, "_espn_state_for",
                            lambda h, a, want_finished=False: sentinel)
        assert lf.live_state_for("Morocco", "France") is sentinel

    def test_primary_hit_still_wins_over_espn(self, monkeypatch):
        import config
        import src.live_feed as lf
        monkeypatch.setattr(config, "API_FOOTBALL_KEY", "some-key")
        fix = {"league": {"id": config.API_FOOTBALL_LEAGUE_ID},
               "teams": {"home": {"id": 1, "name": "Morocco"},
                         "away": {"id": 2, "name": "France"}},
               "fixture": {"id": 9, "status": {"short": "1H", "elapsed": 44,
                                               "extra": None, "long": ""}},
               "goals": {"home": 0, "away": 0}, "events": []}
        monkeypatch.setattr(lf, "_fetch_live_fixtures", lambda: [fix])
        monkeypatch.setattr(lf, "_espn_state_for",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("ESPN must not be called")))
        out = lf.live_state_for("Morocco", "France")
        assert out["is_live"] and out["home_name"] == "Morocco"


class TestEspnDatedEventLookup:
    def test_finds_future_fixture_via_dated_scoreboard(self, monkeypatch):
        """The default ESPN scoreboard is today-only; the reference-odds
        fallback needs fixtures 1-2 days out, looked up by kickoff date."""
        import time as _t

        import src.live_feed as lf
        monkeypatch.setattr(lf, "_espn_states", lambda: [])
        lf._cache["__espnday__20260711"] = (_t.time(), [
            {"id": 760512, "competitions": [{"competitors": [
                {"team": {"displayName": "Norway"}},
                {"team": {"displayName": "England"}}]}]},
        ])
        assert lf._espn_event_id("Norway", "England",
                                 on_date="20260711") == "760512"
        assert lf._espn_event_id("Norway", "England") is None  # today-only
        assert lf._espn_event_id("Argentina", "Switzerland",
                                 on_date="20260711") is None
