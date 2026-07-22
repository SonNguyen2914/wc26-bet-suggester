"""Pins the calibration/significance pipeline to the committed archive so
the narrative numbers in docs/V7 cannot drift from what the script
actually computes (Jul 21 evaluation, patch 4).

HISTORICAL-ARTIFACT SEMANTICS: every assertion here binds to the frozen
2026 WC input version (six lock-bearing matches, settlement backfill of
Jul 21). These are statements about THAT artifact — "this six-match
sample does not support a superiority claim" — not general invariants.
When new independent matches extend the dataset, a legitimately nonzero
interval is a REPORT UPDATE (new input version, new pinned values), not
a model failure. Bootstraps run at n_boot=500 here for suite speed;
determinism is what's under test — the full 10k artifact is
research_archive/calibration_results.json.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import score_calibration as sc  # noqa: E402


@pytest.fixture(scope="module")
def rows():
    return sc.load_rows()


class TestDescriptivePinned:
    def test_row_universe(self, rows):
        assert len(rows) == 293
        assert len({r["match"] for r in rows}) == 6

    def test_brier(self, rows):
        assert sc.brier(rows, "raw") == pytest.approx(0.0897900, abs=1e-6)
        assert sc.brier(rows, "anch") == pytest.approx(0.0895547, abs=1e-6)
        assert sc.brier(rows, "imp") == pytest.approx(0.0910736, abs=1e-6)

    def test_auc(self, rows):
        assert sc.auc(rows, "raw") == pytest.approx(0.8931846, abs=1e-6)
        assert sc.auc(rows, "imp") == pytest.approx(0.8902007, abs=1e-6)

    def test_ece10(self, rows):
        assert sc.ece_width(rows, "raw", 10) == pytest.approx(0.0268635,
                                                              abs=1e-6)
        assert sc.ece_width(rows, "imp", 10) == pytest.approx(0.0384403,
                                                              abs=1e-6)

    def test_ece_ordering_is_binning_sensitive(self, rows):
        # The documented caveat, as an executable fact: raw wins the
        # width-10 spec, anchored wins equal-count-10.
        assert sc.ece_width(rows, "raw", 10) < sc.ece_width(rows, "imp", 10)
        assert sc.ece_count(rows, "anch", 10) < sc.ece_count(rows, "raw", 10)


class TestSignificancePinned:
    def test_binomial_both_sidedness(self):
        one, two = sc.binomial_p(11, 14)
        assert one == pytest.approx(0.0286865234375, abs=1e-12)
        assert two == pytest.approx(0.0573730468750, abs=1e-12)

    def test_advance_scorecard(self):
        hits = sum(1 for _, p, y, _ in sc.ADVANCE_CALLS
                   if (p >= 0.5) == (y == 1))
        assert hits == 11 and len(sc.ADVANCE_CALLS) == 14
        labels = {lab for _, _, _, lab in sc.ADVANCE_CALLS}
        assert labels == {"reconstructed", "prospective-frozen"}

    def test_bootstraps_deterministic(self, rows):
        a1, b1, c1 = sc.cluster_bootstrap(rows, n_boot=500)
        a2, b2, c2 = sc.cluster_bootstrap(rows, n_boot=500)
        assert a1 == a2 and b1 == b2 and c1 == c2
        lo, hi = sc.ci95(a1)
        assert lo == pytest.approx(-0.1069635, abs=1e-6)
        assert hi == pytest.approx(0.1156166, abs=1e-6)
        # For THIS committed six-match artifact, every market-comparison
        # interval straddles zero: the honest headline is parity. A future
        # dataset version that clears zero updates the report AND this pin
        # together — see the module docstring.
        assert lo < 0 < hi
        elo, ehi = sc.ci95(c1)
        assert elo < 0 < ehi


class TestReplaysPinned:
    def test_raw_rule_is_descriptive_replay(self, rows):
        r = sc.replay(rows, "raw")
        assert (r["n"], r["wins"]) == (28, 13)
        assert r["pnl"] == pytest.approx(0.8471, abs=1e-3)

    def test_live_kelly_rule_lost_flat_staked(self, rows):
        # The live bot's anchored-edge gate, flat $1 across all six lock
        # matches: NEGATIVE. The bot's +45% bankroll came from staking and
        # a two-match window, not from the rule being generally
        # profitable — the two replays must never be conflated again.
        r = sc.replay(rows, "anch")
        assert (r["n"], r["wins"]) == (17, 6)
        assert r["pnl"] < 0
