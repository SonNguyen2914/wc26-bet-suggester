"""The ONE execution-economics module (V7 evaluation F5).

Every consumer of trade economics — the suggester's TAKE gate and EV,
the bots' entries and exits, the position tracker's cash-outs, the
replay scripts — imports from HERE, so "fee-aware" can never again be
true in one subsystem and false in another (KELLY was corrected on
Jul 21 while the primary recommendation engine kept gating on gross
edge; that inconsistency was the finding).

Semantics:
  buys fill at the ASK;   all-in cost  q = ask + fee(ask)
  sells fill at the BID;  proceeds     = bid - fee(bid)
  edge, EV, break-even, and Kelly all use the ALL-IN cost.
"""
from __future__ import annotations

FEE_RATE = 0.07                     # Kalshi taker fee: 0.07 * P * (1-P)


def fee(p: float) -> float:
    """Taker fee per contract at price p."""
    return FEE_RATE * p * (1.0 - p)


def all_in_cost(ask: float) -> float:
    """What a buyer actually pays per contract."""
    return ask + fee(ask)


def net_edge(model_p: float, ask: float) -> float:
    """Model probability minus the ALL-IN cost — the number a trade gate
    must clear. Gross edge (p - ask) overstates this by the entry fee,
    which admits marginal trades whose true edge is under the threshold."""
    return model_p - all_in_cost(ask)


def net_ev(model_p: float, ask: float) -> float:
    """Expected net profit per $1 staked at the all-in cost: a winning
    contract pays $1 against cost q, so EV = p*(1-q)/q - (1-p)."""
    q = all_in_cost(ask)
    if q <= 0 or q >= 1:
        return 0.0
    return model_p * (1.0 - q) / q - (1.0 - model_p)


def breakeven_probability(ask: float) -> float:
    """The model probability at which the all-in trade is EV-zero."""
    return all_in_cost(ask)


def sell_proceeds(bid: float | None, contracts: float = 1.0) -> float | None:
    """What an exit actually realizes; None when there is no bid — an
    absent bid means NOT EXECUTABLE, never "use the ask"."""
    if bid is None:
        return None
    return contracts * (bid - fee(bid))


def kelly_fraction(model_p: float, ask: float) -> float:
    """Binary Kelly at the all-in cost (0 when there is no net edge)."""
    q = all_in_cost(ask)
    if q <= 0 or q >= 1:
        return 0.0
    return max(0.0, (model_p - q) / (1.0 - q))
