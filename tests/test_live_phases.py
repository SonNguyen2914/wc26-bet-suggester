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
