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
