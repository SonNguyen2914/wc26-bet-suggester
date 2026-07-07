"""Layer 1 — scoreline spike detection (LOG-ONLY).

Infers "a goal probably just happened" from Kalshi's own score markets,
for free, with no external feed and no latency-induced false edge (we learn
the moment the market learns). It does NOT touch predictions or the board:
it only PRINTS what it thinks changed, so it can be eyeballed against real
matches and its thresholds tuned before anything is allowed to depend on it.

The signal is the SHIFT OF THE DISTRIBUTION, not one contract twitching.
Score markets are thin and jumpy — a single KXWCSCORE-1-0 can swing 20pts
on one small trade with no goal. So we:
  1. read all `score_H_A` markets for a match, treat their yes_prices as an
     (unnormalized) distribution over final-ish scorelines;
  2. find the current most-likely scoreline;
  3. only flag a "suspected goal" when the argmax scoreline changes to one
     whose implied total goals is HIGHER than the previous leader's, the new
     leader clears a minimum confidence, and it beats the old leader by a
     margin — i.e. the whole distribution has re-centered on more goals,
     not merely wobbled.

State is per-match and in-process. Tunables are module constants so they
can be adjusted from what we learn watching live matches.
"""
from __future__ import annotations

import re
import time

# --- tunables (adjust after watching real matches) -------------------------
MIN_LEADER_PRICE = 0.18     # new top scoreline must be at least this likely
MIN_MARGIN = 0.06           # ...and beat the previous leader by this much
DEBOUNCE_SECONDS = 90       # ignore repeat flags for the same match within
COOLDOWN_AFTER_FLAG = 120   # ...and don't re-flag for this long after a flag

_SCORE_RE = re.compile(r"^score_(\d+)_(\d+)$")

# per-match state: match_id -> {"leader": (h,a)|None, "last_flag": ts}
_state: dict[str, dict] = {}


def _score_of(outcome_key: str) -> tuple[int, int] | None:
    m = _SCORE_RE.match(outcome_key or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _leader(markets: list[dict]) -> tuple[tuple[int, int], float] | None:
    """Most-likely scoreline and its yes_price, from the score_* markets."""
    best: tuple[int, int] | None = None
    best_p = -1.0
    for mkt in markets:
        sc = _score_of(mkt.get("outcome_key", ""))
        if sc is None:
            continue
        p = mkt.get("yes_price", 0.0)
        if p > best_p:
            best_p, best = p, sc
    if best is None:
        return None
    return best, best_p


def inspect(match_id: str, markets: list[dict], now: float | None = None
            ) -> dict | None:
    """Feed a match's current markets in; get back a 'suspected goal' event
    (and log it) when the scoreline distribution re-centers on more goals,
    else None. Pure w.r.t. predictions — nothing downstream changes.
    """
    now = time.time() if now is None else now
    lead = _leader(markets)
    if lead is None:
        return None
    (h, a), price = lead

    st = _state.setdefault(match_id, {"leader": None, "last_flag": 0.0})
    prev = st["leader"]

    event = None
    if prev is not None and (h, a) != prev:
        prev_total = prev[0] + prev[1]
        new_total = h + a
        recent = (now - st["last_flag"]) < COOLDOWN_AFTER_FLAG
        # Only a MORE-GOALS re-center, confident enough, past cooldown.
        if (new_total > prev_total and price >= MIN_LEADER_PRICE
                and not recent):
            # margin over where the OLD leader now sits
            old_price = next(
                (m.get("yes_price", 0.0) for m in markets
                 if _score_of(m.get("outcome_key", "")) == prev), 0.0)
            if price - old_price >= MIN_MARGIN:
                event = {
                    "match_id": match_id,
                    "suspected_score": f"{h}-{a}",
                    "previous_score": f"{prev[0]}-{prev[1]}",
                    "leader_price": round(price, 3),
                    "goals_delta": new_total - prev_total,
                    "at": now,
                }
                st["last_flag"] = now
                print(f"[spike] {match_id}: suspected goal — scoreline "
                      f"re-centered {prev[0]}-{prev[1]} -> {h}-{a} "
                      f"(p={price:.2f}). LOG-ONLY, not acting.")

    st["leader"] = (h, a)
    return event


def reset(match_id: str | None = None) -> None:
    """Clear state (a match ended, or tests)."""
    if match_id is None:
        _state.clear()
    else:
        _state.pop(match_id, None)


def current_leader(match_id: str) -> tuple[int, int] | None:
    st = _state.get(match_id)
    return st["leader"] if st else None
