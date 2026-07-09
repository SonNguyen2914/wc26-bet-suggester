"""Prediction cache: latest_for_match must dedup overlapping runs.

Regression guard for the board showing every market TWICE when two prediction
runs for the same match land close together (e.g. a manual refresh-all
overlapping the hourly/boot job).
"""
from src.db import Prediction, SessionLocal, init_db
from src.cache import latest_for_match


def _pred(prob, ev, source):
    return Prediction(
        match_id="TST", market_id="KXWCTOTAL-TST-1",
        market_title="Over 0.5 total goals", outcome_key="over_0_5",
        model_probability=prob, kalshi_odds=1.08, implied_probability=prob - 0.02,
        edge=0.02, expected_value=ev, confidence=0.7, xg_home=1.3, xg_away=1.3,
        scoreline_json="[]", source=source, is_final=False, model_version="v1",
    )


def test_overlapping_runs_dedup_to_latest():
    init_db()
    with SessionLocal() as s:
        s.query(Prediction).filter(Prediction.match_id == "TST").delete()
        s.commit()
        s.add(_pred(0.929, 0.10, "run1")); s.commit()   # first run
        s.add(_pred(0.927, 0.11, "run2")); s.commit()   # overlapping fresher run

    snap = latest_for_match("TST")
    ids = [m["market_id"] for m in snap["markets"]]
    assert ids.count("KXWCTOTAL-TST-1") == 1, "market duplicated across runs"
    # the freshest run's price wins
    assert snap["markets"][0]["model_probability"] == 0.927
    assert snap["source"] == "run2"
