"""Shared model-probability cache.

The odds poller needs the latest model probability per market to compute
edge without re-simulating (the model refreshes hourly; odds don't wait).
This lives in its own module so BOTH the scheduler jobs and the on-demand
API paths can refresh it without a circular import between jobs/scheduler
and api/main.
"""
from __future__ import annotations

_model_probs: dict[str, float] = {}


def refresh_model_cache(result: dict) -> None:
    """Record the latest model probability for every market present in a
    run_for_match() result dict."""
    for s in result.get("suggestions", []):
        _model_probs[s["market_id"]] = s["model_probability"]


def get_model_prob(market_id: str) -> float | None:
    """Latest cached model probability for a market, or None if the model
    hasn't priced it yet this process."""
    return _model_probs.get(market_id)
