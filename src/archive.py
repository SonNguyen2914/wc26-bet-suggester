"""Canonical research-archive serving (V7 evaluation F1).

The six complete T-10 lock bundles are committed, immutable JSON. The
DATABASE copies of those locks die on every deploy wipe and — unlike
results, closings, and the bot ledger — cannot be rebuilt from feeds.
Before this module, the review endpoints silently fell back to a FRESH
post-tournament simulation (current model, current stats) where the
frozen record should be: a provenance failure, found live by the V7
independent evaluation (research/FINAL served final_lock count 0 while
the prediction endpoint served a v2 retro-sim).

This module makes the committed artifact the serving source of truth:
lock rows are read directly from the bundles (no materialization step,
no boot dependency — the files ship in the image), and the retrospective
simulation fallback is REMOVED from historical review routes. A finished
match with no frozen record now says so honestly (`archive_incomplete`)
instead of inventing plausible numbers.

Payload discipline: everything served here is either verbatim from the
frozen rows or arithmetically derived from them (gross EV from frozen
probability and odds). No re-simulation, no current-model numbers. The
summary panel and xG figures are NOT reconstructed — they were not
archived in the trimmed lock rows, so the review page shows the frozen
markets table and nothing invented.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "..",
                           "research_archive")

# The complete prospective bundles: every match whose T-10 lock survived
# to the archive. ESP_BEL and MAR_FRA have closings/results only (their
# locks died in pre-discipline wipes) and are deliberately absent.
LOCK_BUNDLES = {
    "NOR_ENG": "NOR_ENG.json",
    "ARG_SUI": "ARG_SUI.json",
    "SF1": "SF1.json",
    "SF2": "SF2.json",
    "THIRD": "THIRD_research_full_2026-07-19T0150Z.json",
    "FINAL": "FINAL_research_full_2026-07-19T2213Z.json",
}


@lru_cache(maxsize=None)
def _load(match_id: str) -> dict | None:
    fname = LOCK_BUNDLES.get(match_id)
    if not fname:
        return None
    try:
        with open(os.path.join(ARCHIVE_DIR, fname)) as f:
            return json.load(f)
    except Exception as exc:            # a bad artifact must be loud, not fatal
        print(f"[archive] {match_id} bundle unreadable: {exc}")
        return None


def has_lock_bundle(match_id: str) -> bool:
    return _load(match_id) is not None and bool(
        _load(match_id).get("final_lock"))


def available_lock_bundles() -> list[str]:
    return [m for m in LOCK_BUNDLES if has_lock_bundle(m)]


def lock_rows(match_id: str) -> list[dict]:
    """The frozen T-10 rows, verbatim (already in the research shape:
    market_id/market_title/outcome_key/model_probability/kalshi_odds/
    implied_probability/edge/confidence/locked_at)."""
    d = _load(match_id)
    return list(d.get("final_lock") or []) if d else []


def review_payload(match_id: str) -> dict | None:
    """A finished-match review payload built ONLY from the frozen bundle,
    in the same shape as cache.latest_for_match. summary/xg/scorelines
    are None/empty — they were not archived, and this module does not
    invent them; the markets table carries the frozen record."""
    rows = lock_rows(match_id)
    if not rows:
        return None
    locked_at = min((r.get("locked_at") or "" for r in rows), default=None)
    markets = []
    for r in rows:
        p, odds = r.get("model_probability"), r.get("kalshi_odds")
        # gross EV per $1, derived from the frozen fields themselves —
        # display parity with the live-era board (which was also gross;
        # the fee-aware board postdates the tournament)
        ev = (round(p * (odds - 1) - (1 - p), 4)
              if p is not None and odds else None)
        markets.append({
            "market_id": r.get("market_id"),
            "market_title": r.get("market_title"),
            "outcome_key": r.get("outcome_key"),
            "model_probability": p,
            "kalshi_odds": odds,
            "implied_probability": r.get("implied_probability"),
            "edge": r.get("edge"),
            "expected_value": ev,
        })
    markets.sort(key=lambda m: (m["expected_value"] is None,
                                -(m["expected_value"] or 0)))
    return {
        "match_id": match_id,
        "generated_at": locked_at,
        "age_seconds": 0,
        "is_stale": False,
        "source": "canonical_archive",
        "is_final": True,
        "xg": None,                     # not archived; never re-simulated
        "scorelines": [],
        "summary": None,
        "confidence": max((r.get("confidence") or 0 for r in rows),
                          default=None),
        "markets": markets,
    }
