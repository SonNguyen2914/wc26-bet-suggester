"""The position tracker — the deferred half of the buy/sell-alert request.

Son records his REAL Kalshi positions here; every live cycle prices each
one's hold-to-settlement EV against its cash-out-now value (both fee-aware)
and pushes an EXIT/HOLD read the moment the comparison flips. Alerts ride
the same fan-out as the signals (Discord + ntfy), so nothing depends on a
page being open.

Honesty contract: cash-out value is computed at the last polled YES price
minus the taker fee — real exits hit the BID, so cash-out is optimistic by
the spread (2-5c on thin books). The HOLD side has no such bias: settlement
pays $1 per contract, entry costs are sunk.
"""
from __future__ import annotations

import time

from sqlalchemy import select

import config
from src.alerts import send_alert
from src.db import SessionLocal, TrackedPosition, utcnow


def fee(p: float) -> float:
    return 0.07 * p * (1.0 - p)


# verdict cooldown/flip state, in-memory (restart cost: one repeat alert)
_state: dict[int, dict] = {}


def _verdict(p_model: float, price: float, contracts: int, cost: float):
    """(verdict, hold_ev, cashout) — EXIT when selling now beats holding to
    settlement by the margin; HOLD when holding beats selling; else CLOSE."""
    hold_ev = contracts * p_model
    cashout = contracts * (price - fee(price))
    margin = config.POSITION_FLIP_MARGIN * max(cost, 1.0)
    if cashout - hold_ev >= margin:
        return "EXIT", hold_ev, cashout
    if hold_ev - cashout >= margin:
        return "HOLD", hold_ev, cashout
    return "CLOSE_CALL", hold_ev, cashout


def evaluate_positions(rows_by_market: dict, match_id: str,
                       minute=None, alert: bool = False) -> list[dict]:
    """One pass over open tracked positions for a match. `rows_by_market`
    are live_auto (or pre-match batch) rows keyed by market_id, carrying
    live_model_probability/market_probability (live keys preferred, batch
    keys as fallback). Persists nothing; fires EXIT alerts on flips."""
    out = []
    with SessionLocal() as s:
        open_pos = s.execute(
            select(TrackedPosition)
            .where(TrackedPosition.match_id == match_id,
                   TrackedPosition.closed_at.is_(None))
        ).scalars().all()
        for pos in open_pos:
            r = rows_by_market.get(pos.market_id)
            if r is None:
                continue
            p = r.get("live_model_probability")
            if p is None:
                p = r.get("model_probability")
            c = r.get("market_probability")
            if c is None:
                c = r.get("implied_probability")
            if p is None or c is None:
                continue
            verdict, hold_ev, cashout = _verdict(p, c, pos.contracts, pos.cost)
            item = {"id": pos.id, "market_id": pos.market_id,
                    "market_title": pos.market_title,
                    "match_id": pos.match_id,
                    "entry_price": pos.entry_price,
                    "contracts": pos.contracts, "cost": pos.cost,
                    "live_probability": round(p, 4), "price": c,
                    "hold_ev": round(hold_ev, 2),
                    "cashout_now": round(cashout, 2),
                    "verdict": verdict,
                    "net_if_hold_wins": round(pos.contracts - pos.cost, 2),
                    "net_if_cashout": round(cashout - pos.cost, 2)}
            out.append(item)
            if alert:
                _maybe_alert(pos, item, minute)
    return out


def _maybe_alert(pos, item: dict, minute) -> None:
    """Push on flips INTO exit territory (and back to strong hold), with the
    signals cooldown so a wobbling book can't spam."""
    now = time.time()
    prev = _state.get(pos.id)
    verdict = item["verdict"]
    if prev and prev["verdict"] == verdict:
        return
    if prev and now - prev["ts"] < config.LIVE_SIGNAL_COOLDOWN_SECONDS:
        return
    _state[pos.id] = {"verdict": verdict, "ts": now}
    if prev is None and verdict != "EXIT":
        return                       # first sighting, nothing urgent
    at = f" @ {minute:.0f}'" if isinstance(minute, (int, float)) else ""
    if verdict == "EXIT":
        send_alert(
            f"💼 CASH-OUT read{at}: {pos.market_title}\n"
            f"Selling now ≈ ${item['cashout_now']:.0f} beats holding "
            f"(EV ${item['hold_ev']:.0f}; live {item['live_probability']:.0%} "
            f"vs {item['price']:.2f} price). Net if you cash: "
            f"{item['net_if_cashout']:+.0f}")
    elif verdict == "HOLD" and prev and prev["verdict"] == "EXIT":
        send_alert(
            f"💼 back to HOLD{at}: {pos.market_title} — live "
            f"{item['live_probability']:.0%} vs {item['price']:.2f}; "
            f"holding beats cashing again.")
