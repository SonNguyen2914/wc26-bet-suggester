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

    def test_all_remaining_teams_have_rates(self):
        # rates track the CURRENT round's survivors (semifinalists since
        # 2026-07-12); eliminated teams drop out of player_rates.json
        from src.player_props import team_players
        for t in ("France", "Spain", "England", "Argentina"):
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
        monkeypatch.setattr(ro, "_kambi_exact_score", lambda m, p: None)
        ro._cache.clear()
        out = ro.reference_odds(self._match(), None)
        assert out["available"] is False and "reason" in out

    def test_unknown_fixture_degrades(self, monkeypatch):
        import src.reference_odds as ro
        monkeypatch.setattr(ro, "_fixture_id", lambda h, a: None)
        monkeypatch.setattr(ro, "_espn_reference", lambda m, p: None)
        monkeypatch.setattr(ro, "_kambi_exact_score", lambda m, p: None)
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


class TestEspnSummaryCachePoisoning:
    def test_oddsless_summary_is_not_cached(self, monkeypatch):
        """Regression: ARG_SUI stuck 'unavailable' on prod — an ESPN summary
        that arrived WITHOUT pickcenter was cached for 10 minutes, blinding
        every retry. Empty answers must never be cached."""
        import src.reference_odds as ro
        from src.schedule_data import get_match
        m = get_match("ARG_SUI")
        ro._cache.clear()
        calls = {"n": 0}

        class _Resp:
            def __init__(self, payload): self._p = payload
            def json(self): return self._p

        def fake_get(url, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp({"pickcenter": []})          # odds not posted yet
            return _Resp({"pickcenter": [{                # posted on retry
                "provider": {"name": "DraftKings"},
                "homeTeamOdds": {"moneyLine": -145,
                                 "team": {"displayName": "Argentina"}},
                "awayTeamOdds": {"moneyLine": 400,
                                 "team": {"displayName": "Switzerland"}},
                "drawOdds": {"moneyLine": 300.0},
                "overUnder": 2.5, "overOdds": -120.0, "underOdds": 100.0}]})

        import src.live_feed as lf
        monkeypatch.setattr(lf, "_espn_event_id", lambda h, a, on_date=None: "760513")
        monkeypatch.setattr(ro, "_model_lookup", lambda s, l, p: None)
        import requests as _rq_mod
        monkeypatch.setattr(_rq_mod, "get", fake_get)

        assert ro._espn_reference(m, None) is None        # first: no odds
        out = ro._espn_reference(m, None)                 # retry refetches
        assert out is not None and out["available"]
        assert calls["n"] == 2                            # empty wasn't cached


class TestKambiExactScore:
    """Unibet's Correct Score via Kambi's keyless CDN — the exact-score
    fill for matches Kalshi hasn't listed yet."""

    def _seed(self, ro, match_id, home, away, outcomes):
        import time as _t
        ro._cache.clear()
        ro._cache["__kambi_events__"] = (_t.time(), [
            {"id": 555, "homeName": home, "awayName": away}])
        ro._cache[f"kambi|{match_id}"] = (_t.time(), [
            {"criterion": {"label": "Correct Score"}, "outcomes": outcomes}])

    def test_scores_odds_and_model_join(self):
        import src.reference_odds as ro
        from src.schedule_data import get_match
        m = get_match("NOR_ENG")
        self._seed(ro, m.match_id, "Norway", "England", [
            {"homeScore": 1, "awayScore": 0, "odds": 12000},
            {"homeScore": 0, "awayScore": 2, "odds": 5500},
            {"homeScore": 5, "awayScore": 2, "odds": None},   # suspended
        ])
        pred = {"scorelines": [{"score": "0-2", "prob": 0.13}]}
        grp = ro._kambi_exact_score(m, pred)
        rows = {r["label"]: r for r in grp["rows"]}
        assert rows["1-0"]["odd"] == 12.0            # milli -> decimal
        assert rows["0-2"]["model"] == 0.13          # exact scoreline join
        assert "5-2" not in rows                     # no price, no row
        # sorted most-likely first
        assert grp["rows"][0]["label"] == "0-2"

    def test_orientation_flips_when_kambi_home_differs(self):
        # Kambi lists England as home; our schedule is NOR_ENG. An England
        # 2-0 must surface as OUR 0-2 — names win, positions lie.
        import src.reference_odds as ro
        from src.schedule_data import get_match
        m = get_match("NOR_ENG")
        self._seed(ro, m.match_id, "England", "Norway", [
            {"homeScore": 2, "awayScore": 0, "odds": 5500},
        ])
        grp = ro._kambi_exact_score(m, None)
        assert [r["label"] for r in grp["rows"]] == ["0-2"]

    def test_fill_appends_group_and_marks_source(self, monkeypatch):
        import src.reference_odds as ro
        from src.schedule_data import get_match
        m = get_match("NOR_ENG")
        grp = {"name": "Exact score · 90 min",
               "rows": [{"label": "1-0", "odd": 12.0,
                         "implied": 0.0833, "books": 1}]}
        monkeypatch.setattr(ro, "_kambi_exact_score", lambda mm, p: grp)
        # case 1: espn fallback payload (winner only) -> group appended
        out = {"available": True, "source": "draftkings via espn",
               "groups": [{"name": "Winner · 90 min", "rows": []}]}
        out = ro._fill_exact_score(out, m, None)
        assert any("Exact score" in g["name"] for g in out["groups"])
        assert "kambi" in out["source"]
        # case 2: everything else down -> kambi-only payload with the note
        out = ro._fill_exact_score(
            {"available": False, "reason": "plan blocked"}, m, None)
        assert out["available"] and out["source"] == "unibet via kambi"
        assert "plan blocked" in out["note"]
        # case 3: a real exact-score group present -> kambi never replaces it
        marker = {"name": "Exact score · 90 min", "rows": ["sentinel"]}
        out = ro._fill_exact_score(
            {"available": True, "source": "api-football",
             "groups": [marker]}, m, None)
        assert out["groups"] == [marker] and out["source"] == "api-football"


class TestLiveEmptyBackoff:
    def test_empty_live_all_backs_off_instead_of_burning_budget(self, monkeypatch):
        """The 15s live tick re-asks live=all every cache window; on the
        season-blind free plan every answer is [] and would torch the daily
        cap. Empty answers must be held for LIVE_EMPTY_BACKOFF_SECONDS."""
        import config
        import src.live_feed as lf
        monkeypatch.setattr(config, "API_FOOTBALL_KEY", "some-key")
        lf._cache.clear()
        calls = {"n": 0}

        def counting_request(path, params):
            calls["n"] += 1
            return {"response": []}

        monkeypatch.setattr(lf, "_request", counting_request)
        monkeypatch.setattr(lf, "_espn_state_for",
                            lambda h, a, want_finished=False: None)
        for _ in range(5):
            lf.live_state_for("Morocco", "France")
        assert calls["n"] == 1          # one real pull; backoff held the rest

    def test_nonempty_uses_normal_cache_window(self, monkeypatch):
        import time as _t

        import config
        import src.live_feed as lf
        monkeypatch.setattr(config, "API_FOOTBALL_KEY", "some-key")
        lf._cache.clear()
        fix = {"league": {"id": config.API_FOOTBALL_LEAGUE_ID},
               "teams": {"home": {"id": 1, "name": "Morocco"},
                         "away": {"id": 2, "name": "France"}},
               "fixture": {"id": 9, "status": {"short": "1H", "elapsed": 10,
                                               "extra": None, "long": ""}},
               "goals": {"home": 0, "away": 0}, "events": []}
        calls = {"n": 0}

        def counting_request(path, params):
            calls["n"] += 1
            return {"response": [fix]}

        monkeypatch.setattr(lf, "_request", counting_request)
        lf.live_state_for("Morocco", "France")
        lf.live_state_for("Morocco", "France")
        assert calls["n"] == 1          # inside the 20s window
        # age the NON-empty cache past the normal window -> refetches
        ts, val = lf._cache[lf._LIVE_ALL_KEY]
        lf._cache[lf._LIVE_ALL_KEY] = (
            _t.time() - config.API_FOOTBALL_CACHE_SECONDS - 1, val)
        lf.live_state_for("Morocco", "France")
        assert calls["n"] == 2


class TestZeroMarketPrediction:
    def test_prediction_with_no_kalshi_markets_is_200_not_500(self, monkeypatch):
        """Regression: prediction/SF1 500'd on prod — a match matching zero
        Kalshi events persists zero Prediction rows, latest_for_match gave
        None, and {**None} raised. Placeholder-sided matches must serve the
        simulation with an empty markets list."""
        from fastapi.testclient import TestClient
        import api.main as main
        from api.main import app
        from src.db import Prediction, SessionLocal, init_db
        init_db()
        with SessionLocal() as s:   # drop any stale local rows for SF1
            s.query(Prediction).filter(Prediction.match_id == "SF1").delete()
            s.commit()
        monkeypatch.setattr(main.engine.kalshi, "get_markets_for_match",
                            lambda m: [])
        client = TestClient(app)
        r = client.get("/api/prediction/SF1?force_refresh=true")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["markets"] == []
        assert d["freshness"] == "fresh"
        ft = d["summary"]["full_time"]
        assert abs(ft["home_win"] + ft["draw"] + ft["away_win"] - 1) < 0.02


class TestResearchSnapshots:
    """Market-side counterpart of the T-10 model lock: the closing/settlement
    snapshot captured when a match freezes (the MAR-FRA lesson)."""

    def _clear(self, match_id):
        from src.db import MarketClosing, SessionLocal, init_db
        init_db()
        with SessionLocal() as s:
            s.query(MarketClosing).filter(
                MarketClosing.match_id == match_id).delete()
            s.commit()

    def test_capture_is_one_shot_and_stores_raw(self, monkeypatch):
        import src.research as research
        from src.db import MarketClosing, SessionLocal
        from src.schedule_data import get_match
        m = get_match("MAR_FRA")
        self._clear(m.match_id)
        calls = {"n": 0}

        def fake(sess, family, home, away):
            calls["n"] += 1
            if family != "KXWCGAME":
                return []
            return [("KXWCGAME-26JUL09FRAMAR",
                     {"ticker": "KXWCGAME-26JUL09FRAMAR-FRA",
                      "title": "France wins?", "status": "settled",
                      "result": "yes", "yes_bid_dollars": "0.99",
                      "yes_ask_dollars": "1.00", "volume": 12345})]

        monkeypatch.setattr(research, "_fetch_family_markets", fake)
        out = research.capture_closing_snapshot(m)
        assert out == {"status": "captured", "markets": 1}
        assert calls["n"] == len(research.FAMILIES)
        # idempotent: second call touches nothing and fetches nothing
        calls["n"] = 0
        out2 = research.capture_closing_snapshot(m)
        assert out2["status"] == "exists" and calls["n"] == 0
        rows = research.closing_rows(m.match_id)
        assert rows[0]["result"] == "yes" and rows[0]["status"] == "settled"
        assert rows[0]["market_id"] == "KXWCGAME-26JUL09FRAMAR-FRA"
        self._clear(m.match_id)

    def test_capture_never_raises(self, monkeypatch):
        import src.research as research
        from src.schedule_data import get_match
        m = get_match("MAR_FRA")
        self._clear(m.match_id)

        def boom(sess, family, home, away):
            raise RuntimeError("kalshi down")

        monkeypatch.setattr(research, "_fetch_family_markets", boom)
        out = research.capture_closing_snapshot(m)   # must not raise
        assert out["status"] == "empty"

    def test_research_endpoint_shape(self, monkeypatch):
        import json as _json

        from fastapi.testclient import TestClient

        import src.research as research
        from api.main import app
        from src.db import MarketClosing, SessionLocal, utcnow
        from src.schedule_data import get_match
        m = get_match("MAR_FRA")
        self._clear(m.match_id)
        with SessionLocal() as s:
            s.add(MarketClosing(
                match_id=m.match_id, market_id="TK-1", event_ticker="EV-1",
                captured_at=utcnow(),
                data_json=_json.dumps({"status": "settled", "result": "no",
                                       "title": "Draw?"})))
            s.commit()
        r = TestClient(app).get(f"/api/research/{m.match_id}")
        assert r.status_code == 200
        d = r.json()
        for k in ("result", "final_lock", "closing", "last_readings"):
            assert k in d, k
        assert d["closing"][0]["result"] == "no"
        assert TestClient(app).get("/api/research/NOPE").status_code == 404
        self._clear(m.match_id)


class TestRestoreMissingResults:
    """Self-heal for the ephemeral-DB wipe: finished matches re-freeze from
    ESPN's dated scoreboard once their live window has closed."""

    def test_restores_wiped_result_and_snapshots(self, monkeypatch):
        import src.live_feed as lf
        import src.live_state as ls
        import src.research as research
        from src.db import MatchResult, SessionLocal, init_db
        init_db()
        with SessionLocal() as s:      # simulate the wipe
            s.query(MatchResult).filter(
                MatchResult.match_id == "MAR_FRA").delete()
            s.commit()

        def fake_state(home, away, want_finished=False, on_date=None):
            if {home, away} == {"Morocco", "France"} and want_finished:
                return {"home_name": "Morocco", "away_name": "France",
                        "home_goals": 0, "away_goals": 2,
                        "status_short": "FT", "is_live": False,
                        "is_finished": True, "red_home": 0, "red_away": 0,
                        "minutes_elapsed": 90.0, "goals_list": []}
            return None

        captured = []
        monkeypatch.setattr(lf, "_espn_state_for", fake_state)
        monkeypatch.setattr(research, "capture_closing_snapshot",
                            lambda m: captured.append(m.match_id) or
                            {"status": "exists"})
        out = ls.restore_missing_results()
        assert out["restored"] >= 1
        with SessionLocal() as s:
            row = s.get(MatchResult, "MAR_FRA")
        assert row is not None and row.home_goals == 0 and row.away_goals == 2
        assert "MAR_FRA" in captured
        # second run: nothing to heal
        captured.clear()
        out2 = ls.restore_missing_results()
        assert "MAR_FRA" not in captured
        assert out2["restored"] == 0 or "MAR_FRA" not in captured


class TestNoFtCardFlood:
    def test_restored_old_result_never_rides_the_ft_window(self, monkeypatch):
        """Regression: a wipe+boot restore stamped finished_at=now on nine
        old results and the landing page flooded with 'just finished' FT
        cards. Restored results carry their real finish time AND the
        scoreboard refuses FT cards for matches that kicked off long ago."""
        import src.live_feed as lf
        import src.live_state as ls
        import src.research as research
        from src.db import MatchResult, SessionLocal, init_db
        init_db()
        with SessionLocal() as s:
            s.query(MatchResult).filter(
                MatchResult.match_id == "CAN_MAR").delete()
            s.commit()

        def fake_state(home, away, want_finished=False, on_date=None):
            if {home, away} == {"Canada", "Morocco"} and want_finished:
                return {"home_name": "Canada", "away_name": "Morocco",
                        "home_goals": 0, "away_goals": 3,
                        "status_short": "FT", "is_live": False,
                        "is_finished": True, "red_home": 0, "red_away": 0,
                        "minutes_elapsed": 90.0, "goals_list": []}
            return None

        monkeypatch.setattr(lf, "_espn_state_for", fake_state)
        monkeypatch.setattr(research, "capture_closing_snapshot",
                            lambda m: {"status": "exists"})
        ls.restore_missing_results()
        with SessionLocal() as s:
            row = s.get(MatchResult, "CAN_MAR")
        assert row is not None
        # finished_at ~ July 4 kickoff + 2h30, nowhere near "now"
        assert ls._aware(row.finished_at) < ls.utcnow() - ls.FT_WINDOW
        # and even with a fresh finished_at, the kickoff guard blocks it
        with SessionLocal() as s:
            row = s.get(MatchResult, "CAN_MAR")
            row.finished_at = ls.utcnow()
            s.commit()
        entries = ls.scoreboard_entries()
        assert all(e["match_id"] != "CAN_MAR" for e in entries)


class TestSettledReviewPage:
    """A finished match's prediction endpoint is a REVIEW page: it serves
    the T-10 locked batch (likelihood/odds/edge as committed pre-kickoff)
    and never re-simulates into an empty table."""

    def _seed(self, match_id, is_final, prob=0.61):
        import json as _json

        from src.db import Prediction, SessionLocal, utcnow
        with SessionLocal() as s:
            s.add(Prediction(
                match_id=match_id, market_id=f"TK-{int(is_final)}",
                market_title="France to win", outcome_key="away_win",
                model_probability=prob, kalshi_odds=1.8,
                implied_probability=0.55, edge=prob - 0.55,
                expected_value=0.1, confidence=0.6, xg_home=1.0, xg_away=1.8,
                scoreline_json="[]", summary_json=_json.dumps(
                    {"full_time": {"home_win": 0.2, "draw": 0.2,
                                   "away_win": 0.6}}),
                source="final_lock" if is_final else "on_demand",
                is_final=is_final, model_version="t"))
            s.commit()

    def _clean(self, match_id):
        from src.db import MatchResult, Prediction, SessionLocal, init_db
        init_db()
        with SessionLocal() as s:
            s.query(Prediction).filter(
                Prediction.match_id == match_id).delete()
            s.commit()

    def test_locked_batch_served_and_no_fresh_run(self, monkeypatch):
        from fastapi.testclient import TestClient
        import api.main as main
        from api.main import app
        mid = "MAR_FRA"                    # has a MatchResult (finished)
        self._clean(mid)
        self._seed(mid, is_final=False, prob=0.55)
        self._seed(mid, is_final=True, prob=0.61)

        def boom(*a, **k):
            raise AssertionError("finished match must not re-simulate")

        monkeypatch.setattr(main.engine, "run_for_match", boom)
        r = TestClient(app).get(f"/api/prediction/{mid}?force_refresh=true")
        assert r.status_code == 200
        d = r.json()
        assert d["freshness"] == "locked" and d["is_stale"] is False
        assert d["markets"][0]["model_probability"] == 0.61   # the LOCK
        self._clean(mid)

    def test_wiped_history_serves_honest_empty(self, monkeypatch):
        from fastapi.testclient import TestClient
        import api.main as main
        from api.main import app
        mid = "MAR_FRA"
        self._clean(mid)
        monkeypatch.setattr(main.engine, "run_for_match",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("no fresh run")))
        r = TestClient(app).get(f"/api/prediction/{mid}")
        assert r.status_code == 200
        d = r.json()
        assert d["markets"] == [] and d["summary"]["full_time"]


class TestLiveMatchStats:
    def test_rows_oriented_by_name_and_pct_formatted(self, monkeypatch):
        import src.live_feed as lf

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                return {"boxscore": {"teams": [
                    {"team": {"displayName": "Belgium"},
                     "statistics": [
                         {"name": "possessionPct", "displayValue": "34"},
                         {"name": "passPct", "displayValue": "0.8"}]},
                    {"team": {"displayName": "Spain"},
                     "statistics": [
                         {"name": "possessionPct", "displayValue": "66"},
                         {"name": "passPct", "displayValue": "0.9"}]},
                ]}}

        lf._cache.pop("__stats__" + lf._norm("Spain") + "|" + lf._norm("Belgium"), None)
        monkeypatch.setattr(lf, "_espn_event_id", lambda h, a, on_date=None: "760511")
        monkeypatch.setattr(lf.requests, "get", lambda *a, **k: _Resp())
        out = lf.espn_match_stats("Spain", "Belgium")
        assert out["available"]
        rows = {r["key"]: r for r in out["rows"]}
        # ESPN listed Belgium first — orientation must follow NAMES
        assert rows["possessionPct"]["home"] == "66"
        assert rows["possessionPct"]["away"] == "34"
        assert rows["passPct"]["home"] == "90"   # 0.9 -> 90%


class TestLiveAutoLevers:
    def _stats(self, sot_h, sot_a, sh_h, sh_a):
        return {"available": True, "rows": [
            {"key": "shotsOnTarget", "home": str(sot_h), "away": str(sot_a)},
            {"key": "totalShots", "home": str(sh_h), "away": str(sh_a)}]}

    def test_neutral_without_inputs(self):
        from src.live_auto import suggest_levers
        assert suggest_levers(None, 1.2, self._stats(3, 1, 9, 2), 60)["source"] == "neutral"
        assert suggest_levers(1.5, 1.2, {"available": False}, 60)["source"] == "neutral"

    def test_shrinks_early_and_caps_extremes(self):
        from src.live_auto import LEVER_CAP_HI, LEVER_CAP_LO, suggest_levers
        early = suggest_levers(1.5, 1.5, self._stats(4, 0, 10, 1), 10)
        late = suggest_levers(1.5, 1.5, self._stats(4, 0, 10, 1), 85)
        # same evidence speaks louder later
        assert abs(early["home"] - 1) < abs(late["home"] - 1)
        wild = suggest_levers(1.5, 1.5, self._stats(15, 0, 30, 0), 90)
        assert wild["home"] <= LEVER_CAP_HI and wild["away"] >= LEVER_CAP_LO
        # dominant side up, quiet side down
        assert late["home"] > 1 > late["away"]

    def test_phase_mapping(self):
        from src.live_auto import _phase_from_status
        assert _phase_from_status("ET") == "et"
        assert _phase_from_status("P") == "pens"
        assert _phase_from_status("2H") == "regulation"

    def test_no_snapshot_degrades(self):
        from src.db import MatchLiveSnapshot, SessionLocal, init_db
        from src.live_auto import live_auto
        from src.schedule_data import get_match
        init_db()
        with SessionLocal() as s:
            s.query(MatchLiveSnapshot).filter(
                MatchLiveSnapshot.match_id == "NOR_ENG").delete()
            s.commit()
        out = live_auto(get_match("NOR_ENG"), None, None)
        assert out["available"] is False


class TestFirstGoalSettledFilter:
    def test_first_goal_props_drop_once_a_goal_exists(self):
        """Caught live in ESP-BEL: 'Spain to score first' re-simulated at
        73% while Spain had already scored first. Once any goal exists the
        first-goal race is settled — never re-priced."""
        from src.schedule_data import get_match
        from src.suggester import SuggesterEngine
        eng = SuggesterEngine()
        m = get_match("ESP_BEL")
        fake = [
            {"market_id": "T-FTTS", "title": "Spain to score first",
             "outcome_key": "home_first_goal", "yes_price": 0.5,
             "decimal_odds": 2.0, "volume_24h": 0.0},
            {"market_id": "T-GAME", "title": "Spain to win",
             "outcome_key": "home_win", "yes_price": 0.5,
             "decimal_odds": 2.0, "volume_24h": 0.0},
        ]
        with_goal = eng.price_live(m, 1, 1, 50, first_goal_scored=True,
                                   markets=fake)
        keys = {r["outcome_key"] for r in with_goal["markets"]}
        assert "home_win" in keys            # real book still priced
        # settled race dropped from BOTH open-market and model-only rows
        assert not keys & {"home_first_goal", "away_first_goal", "no_goal"}
        goalless = eng.price_live(m, 0, 0, 20, first_goal_scored=False,
                                  markets=fake)
        keys0 = {r["outcome_key"] for r in goalless["markets"]}
        assert "home_first_goal" in keys0    # still a live race at 0-0


class TestSimMinutesClamp:
    def test_period_stoppage_never_leaks_into_the_next_period(self):
        """Regression: ESP-BEL at 45'+5' fed the sim 50 minutes 'played',
        silently eating five minutes of the second half; 2H stoppage would
        likewise masquerade as extra time."""
        from src.live_auto import sim_minutes
        # stoppage caps: progress never leaks into the next period...
        assert sim_minutes(50.0, "1H") == 44.0     # 45'+5' -> half still open
        assert sim_minutes(48.0, "HT") == 45.0     # break: half the match left
        assert sim_minutes(50.0, "2H") == 50.0     # genuine 2H time intact
        assert sim_minutes(30.0, "1H") == 30.0     # normal time untouched
        # ...but a RUNNING period keeps a small remainder on the clock:
        # no API announces added time, yet at 90'+4' the match observably
        # isn't over — pricing it as finished undervalues late goals.
        assert sim_minutes(94.0, "2H") == 88.0
        assert sim_minutes(124.0, "ET") == 118.0
        assert sim_minutes(100.0, "P") == 120.0


class TestModelFirstLiveBoard:
    def test_model_only_rows_fill_the_closed_books(self):
        """In play Kalshi closes settled/impossible books, leaving a thin
        open list — the live read must still show the full model board,
        with empty market columns where no book is open."""
        from src.schedule_data import get_match
        from src.suggester import SuggesterEngine
        eng = SuggesterEngine()
        m = get_match("ESP_BEL")
        fake = [{"market_id": "T-GAME", "title": "Spain to win",
                 "outcome_key": "home_win", "yes_price": 0.5,
                 "decimal_odds": 2.0, "volume_24h": 0.0}]
        out = eng.price_live(m, 1, 1, 76, markets=fake,
                             first_goal_scored=True)
        rows = {r["outcome_key"]: r for r in out["markets"]}
        # totals ladder, margins, winner trio, advance + method all present
        for k in ("over_2_5", "over_4_5", "btts", "home_margin_2",
                  "away_margin_3", "draw", "away_win",
                  "home_advance", "home_win_pens"):
            assert k in rows, k
        # exact scores from the remaining-sim distribution appear too
        assert any(k.startswith("score_") for k in rows)
        # model-only rows carry no market columns; the real book does
        assert rows["over_4_5"]["market_probability"] is None
        assert rows["over_4_5"].get("model_only") is True
        assert rows["home_win"]["market_probability"] == 0.5

    def test_et_continuation_still_restricts_model_rows(self):
        from src.schedule_data import get_match
        from src.suggester import SuggesterEngine
        eng = SuggesterEngine()
        m = get_match("ESP_BEL")
        out = eng.price_live(m, 1, 1, 100, phase="et", markets=[],
                             first_goal_scored=True)
        keys = {r["outcome_key"] for r in out["markets"]}
        allowed = {"home_advance", "away_advance", "home_win_et",
                   "away_win_et", "home_win_pens", "away_win_pens"}
        assert keys and keys <= allowed


class TestOpennessDefenceLevers:
    """The volume half of the live levers: attack levers redistribute
    chances (share), the defence levers scale the whole goal environment
    from total shot volume vs the xG-implied expectation."""

    def _stats(self, sot_h, sot_a, sh_h, sh_a):
        return {"available": True, "rows": [
            {"key": "shotsOnTarget", "home": str(sot_h), "away": str(sot_a)},
            {"key": "totalShots", "home": str(sh_h), "away": str(sh_a)}]}

    def test_open_game_raises_both_defence_levers(self):
        from src.live_auto import suggest_levers
        # 1.2+1.2 damped xG expects ~19 weighted shots by 75'; 13 SoT + 30
        # shots is a shootout
        lv = suggest_levers(1.2, 1.2, self._stats(7, 6, 16, 14), 75)
        assert lv["def_home"] == lv["def_away"] > 1.0
        assert lv["basis"]["openness_raw"] > 1.0

    def test_locked_game_lowers_both(self):
        from src.live_auto import suggest_levers
        lv = suggest_levers(1.5, 1.5, self._stats(1, 0, 3, 2), 80)
        assert lv["def_home"] == lv["def_away"] < 1.0

    def test_caps_and_neutral(self):
        from src.live_auto import DEF_CAP_HI, DEF_CAP_LO, suggest_levers
        wild = suggest_levers(0.8, 0.8, self._stats(15, 12, 30, 25), 85)
        assert wild["def_home"] <= DEF_CAP_HI
        quiet = suggest_levers(2.0, 2.0, self._stats(0, 0, 1, 0), 85)
        assert quiet["def_home"] >= DEF_CAP_LO
        assert suggest_levers(None, 1.2, self._stats(3, 1, 9, 2), 60)["def_home"] == 1.0

    def test_shrinks_early(self):
        from src.live_auto import suggest_levers
        # same shot volume speaks louder at 80' than at 15'... but at 15'
        # the same absolute volume is objectively wilder, so compare the
        # SHRINK: early lever must sit closer to 1 than the raw signal
        early = suggest_levers(1.2, 1.2, self._stats(4, 3, 8, 7), 15)
        assert 1.0 < early["def_home"] < early["basis"]["openness_raw"]

    def test_defence_mult_moves_goal_probabilities(self):
        from src.schedule_data import get_match
        from src.suggester import SuggesterEngine
        eng = SuggesterEngine()
        m = get_match("SF1")
        base = eng.price_live(m, 0, 0, 45.0, markets=[])
        open_ = eng.price_live(m, 0, 0, 45.0, markets=[],
                               defence_home_mult=1.2, defence_away_mult=1.2)
        def p(res, key):
            return next(r["live_model_probability"] for r in res["markets"]
                        if r["outcome_key"] == key)
        # leakier defences -> more goals -> Over 2.5 up, echo transparent
        assert p(open_, "over_2_5") > p(base, "over_2_5")
        assert open_["defence_levers"] == {"home": 1.2, "away": 1.2}
