"""KXWCSCORE / SPREAD / MOV classification: ticker team-codes must map to OUR
home/away sides, never positionally.

Regression guard for the flipped-exact-scores bug: Kalshi's event for our
MAR_FRA match is ...FRAMAR (France listed first), so ticker FRA1MAR0 means
France 1 - Morocco 0. The old parser took the digits positionally as
(home, away) = (1, 0), labeling it "Morocco 1-0 France" — flipping the
title, the model probability, and the edge on every asymmetric scoreline.
Spotted live: the board "priced" Morocco 1-0 at 13.5% vs France 1-0 at 5.5%.
"""
from datetime import datetime, timezone

from src.kalshi_client import _classify_outcome, _code_is
from src.schedule_data import Match


def _match(home, away):
    return Match("TST", home, away, "QF",
                 datetime(2026, 7, 9, tzinfo=timezone.utc), stage="knockout")


def _mkt(ticker):
    return {"ticker": ticker, "title": "", "yes_sub_title": ""}


class TestScoreOrientation:
    # Our schedule: home=Morocco, away=France. Kalshi lists FRA first.
    def test_kalshi_order_reversed_from_ours(self):
        m = _match("Morocco", "France")
        ev = "KXWCSCORE-26JUL09FRAMAR"
        # FRA1MAR0 = France 1, Morocco 0 -> our home (MAR) 0, away (FRA) 1
        assert _classify_outcome(m, _mkt("KXWCSCORE-26JUL09FRAMAR-FRA1MAR0"),
                                 ev) == "score_0_1"
        assert _classify_outcome(m, _mkt("KXWCSCORE-26JUL09FRAMAR-FRA0MAR1"),
                                 ev) == "score_1_0"
        assert _classify_outcome(m, _mkt("KXWCSCORE-26JUL09FRAMAR-FRA2MAR1"),
                                 ev) == "score_1_2"
        # symmetric scores are orientation-proof
        assert _classify_outcome(m, _mkt("KXWCSCORE-26JUL09FRAMAR-FRA1MAR1"),
                                 ev) == "score_1_1"

    def test_kalshi_order_matches_ours(self):
        m = _match("Brazil", "Norway")
        ev = "KXWCSCORE-26JUL05BRANOR"
        assert _classify_outcome(m, _mkt("KXWCSCORE-26JUL05BRANOR-BRA2NOR0"),
                                 ev) == "score_2_0"

    def test_non_prefix_fifa_codes(self):
        # ESP/SUI are not prefixes of Spain/Switzerland — FIFA-code map case.
        m = _match("Spain", "Switzerland")
        ev = "KXWCSCORE-26JULXXESPSUI"
        assert _classify_outcome(m, _mkt("KXWCSCORE-26JULXXSUIESP-SUI2ESP0"),
                                 ev) == "score_0_2"

    def test_unrecognized_codes_skipped(self):
        m = _match("Morocco", "France")
        assert _classify_outcome(
            m, _mkt("KXWCSCORE-26JUL09FRAMAR-XXX1YYY0"),
            "KXWCSCORE-26JUL09FRAMAR") is None


class TestSideCodeResolution:
    def test_mov_fifa_code_side(self):
        m = _match("Spain", "Belgium")
        assert _classify_outcome(m, _mkt("KXWCMOV-26JUL10ESPBEL-ESPREG"),
                                 "KXWCMOV-26JUL10ESPBEL") == "home_win"

    def test_spread_non_prefix_code(self):
        # SUI2 previously matched nothing -> Switzerland spreads were dropped.
        m = _match("Argentina", "Switzerland")
        assert _classify_outcome(m, _mkt("KXWCSPREAD-26JUL11ARGSUI-SUI2"),
                                 "KXWCSPREAD-26JUL11ARGSUI") == "away_margin_2"

    def test_code_is_basics(self):
        assert _code_is("SUI", "Switzerland")
        assert _code_is("ESP", "Spain")
        assert _code_is("MAR", "Morocco")      # prefix fallback
        assert not _code_is("FRA", "Morocco")
        assert not _code_is("", "France")


class TestFallbackHardening:
    def test_tielemans_is_not_a_draw(self):
        """Regression: KXWCGOAL/KXWCAST player props ('Youri TIElemans: 1+')
        matched the 'tie' substring and were sold as Draw-after-90 at 93x."""
        m = _match("Spain", "Belgium")
        for tick, fam in (("KXWCGOAL-26JUL10ESPBEL-BELYTIELE8-2", "KXWCGOAL-26JUL10ESPBEL"),
                          ("KXWCAST-26JUL10ESPBEL-BELYTIELE8-1", "KXWCAST-26JUL10ESPBEL")):
            mk = {"ticker": tick, "title": "", "yes_sub_title": "Youri Tielemans: 1+"}
            assert _classify_outcome(m, mk, fam) is None

    def test_unknown_kxwc_family_denied(self):
        m = _match("Spain", "Belgium")
        mk = {"ticker": "KXWCMYSTERY-26JUL10ESPBEL-DRAWX",
              "title": "Draw something", "yes_sub_title": "Draw"}
        assert _classify_outcome(m, mk, "KXWCMYSTERY-26JUL10ESPBEL") is None

    def test_explicit_game_and_advance(self):
        m = _match("Spain", "Belgium")
        assert _classify_outcome(m, _mkt("KXWCGAME-26JUL10ESPBEL-TIE"),
                                 "KXWCGAME-26JUL10ESPBEL") == "draw"
        assert _classify_outcome(m, _mkt("KXWCGAME-26JUL10ESPBEL-ESP"),
                                 "KXWCGAME-26JUL10ESPBEL") == "home_win"
        assert _classify_outcome(m, _mkt("KXWCADVANCE-26JUL10ESPBEL-BEL"),
                                 "KXWCADVANCE-26JUL10ESPBEL") == "away_advance"
