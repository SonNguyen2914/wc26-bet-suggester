"""Execution-quality paper trading (V8.1 evaluation Phase 7).

PAPER ONLY. This simulates what executing the model's positive-edge
signals WOULD have cost, against the FROZEN lock book — it never places
a real order and has zero coupling to the real-money flag. Its purpose
is the execution-strategy evidence the evaluation asks for, kept
strictly separate from forecast quality.

Realism, per the review:
  - entry gates (execution-ready snapshot, quote age, executable ask,
    minimum size, spread, net edge, approved model) — a rejected signal
    keeps its reason, so the ledger has no survivorship bias;
  - fills walk REAL depth (Kalshi's book: buying YES consumes the NO
    bid ladder, yes_ask = 100 - no_bid), consuming size level by level,
    with partial fills when depth runs out — never unlimited size at
    the top;
  - net-of-fee, net-of-slippage economics;
  - a fully-referenced ledger (signal, run, contract, frozen quote,
    requested/filled/price/fee/slippage/latency/reason).

Deterministic: given the frozen quote+depth stream and a fixed policy,
the ledger reproduces exactly (the replay acceptance test). Scoped to
the unambiguous 3-way winner market for v1; prop settlement is a later
extension.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import config
from src.live.db import get_session, plane_ready
from src.live.models import (Fixture, MarketContract, MarketDepthLevel,
                             MarketQuote, MarketSnapshot, PaperFill,
                             PaperSignal, PredictionContract, PredictionRun)
from src.live.runs import approved_model_version

THREE_WAY = ("home_win", "draw", "away_win")

# Kalshi's general trading fee, FROZEN so paper P&L ties to the exact
# rule that produced it (V9 eval F8). The published general formula is
# fee = ceil(0.07 * C * P * (1-P)) in dollars = ceil(7 * C * P * (1-P))
# in cents, computed ONCE on the whole order and rounded UP — never
# rounded per contract (the V8/V9 bug, which zeroed the fee at the
# extremes and flipped sign by price). Series/event overrides and
# maker/taker differences are NOT yet modeled, so paper P&L stays
# explicitly approximate until they are.
FEE_SCHEDULE = {
    "version": "kalshi-general-2026-07",
    "rate": 0.07,
    "rounding": "ceil_per_order",
    "modeled": "general taker fee only",
    "not_modeled": "series/event overrides, maker/taker, per-fill "
                   "accumulation — paper P&L is approximate",
}

# The execution policy is versioned so paper results can be tied to the
# exact rules that produced them.
EXEC_POLICY = {
    "version": "paper-exec-v2",          # v2: order-level fee (V9 eval F8)
    "fee_schedule": FEE_SCHEDULE["version"],
    "min_top_size": 10,       # contracts available at the ask to bother
    "max_spread_c": 8,        # widest yes ask-bid we'll cross
    "max_quote_age_s": 600,   # snapshot freshness ceiling
    "min_net_edge": 0.03,     # model_p - (ask + fee) must clear this
    "target_contracts": 100,  # requested size (depth caps the fill)
    "latency_ms": 250,        # recorded assumption (no movement vs frozen)
}


def _now():
    return datetime.now(timezone.utc)


def order_fee_c(price_c: int | None, contracts: int) -> int:
    """Kalshi general trading fee for a WHOLE order, integer cents,
    rounded up once (V9 eval F8): ceil(7 * C * P * (1-P)). Computing it
    per contract and multiplying (the old bug) is materially wrong — it
    rounds to 0 at the extremes and over/under-charges by price."""
    if not contracts or contracts <= 0 or price_c is None:
        return 0
    p = price_c / 100.0
    return int(math.ceil(FEE_SCHEDULE["rate"] * 100.0 * contracts
                         * p * (1.0 - p)))


def yes_buy_ladder(quote: MarketQuote, depth: list) -> list[tuple[int, int]]:
    """The executable BUY-YES ladder as (yes_ask_c, size), best first.
    Kalshi: a resting NO bid at price q IS a YES ask at 100-q. So we
    walk the NO depth. When no depth was captured, fall back to the top
    quote's ask + size as a single level."""
    levels = []
    for d in depth:
        if d.side == "no" and 0 < d.price_c < 100 and d.size > 0:
            levels.append((100 - d.price_c, d.size))
    if levels:
        levels.sort(key=lambda x: x[0])       # lowest ask first
        return levels
    if quote.yes_ask_c is not None:
        size = quote.yes_ask_size or EXEC_POLICY["min_top_size"]
        return [(quote.yes_ask_c, size)]
    return []


def simulate_fill(ladder: list[tuple[int, int]], requested: int) -> dict:
    """Deterministic depth-walk. Consumes size level by level up the
    book; partial fill when depth is exhausted. Returns avg price,
    filled qty, slippage vs best, levels consumed."""
    filled = 0
    cost = 0
    used = 0
    best = ladder[0][0] if ladder else None
    for price_c, size in ladder:
        if filled >= requested:
            break
        take = min(size, requested - filled)
        filled += take
        cost += take * price_c
        used += 1
    if filled == 0:
        return {"filled": 0, "avg_price_c": None, "best_ask_c": best,
                "slippage_c": None, "levels": 0}
    avg = round(cost / filled)
    return {"filled": filled, "avg_price_c": avg, "best_ask_c": best,
            "slippage_c": avg - best, "levels": used}


def _market_gate(quote, snap, net_edge, model_approved) -> str | None:
    """Delegate to the central risk engine — one policy authority for
    every order path (V8.1 eval Phase 8)."""
    from src.live import risk
    pol = EXEC_POLICY
    return risk.market_gate(
        quote, snap, net_edge, min_net_edge=pol["min_net_edge"],
        min_size=pol["min_top_size"], max_spread_c=pol["max_spread_c"],
        max_quote_age_s=pol["max_quote_age_s"],
        model_approved=model_approved)


def paper_trade_lock(run_id: str) -> dict:
    """Generate paper signals + fills for one canonical lock. Idempotent
    (unique per run+contract). PAPER — no real order is ever placed."""
    if not (plane_ready() and config.MLS_SHADOW_ENABLED
            and config.PAPER_TRADING_ENABLED):
        return {"skipped": "off"}
    s = get_session()
    signals = fills = 0
    try:
        run = s.get(PredictionRun, run_id)
        if run is None or not (run.run_type == "t10" and run.canonical):
            return {"skipped": "not a canonical lock"}
        model_approved = approved_model_version(s) is not None
        snap = (s.get(MarketSnapshot, run.market_snapshot_id)
                if run.market_snapshot_id else None)
        fx = s.get(Fixture, run.fixture_id)
        for c in (s.query(PredictionContract)
                  .filter_by(prediction_run_id=run_id).all()):
            if c.outcome_key not in THREE_WAY or not c.market_quote_id \
                    or not c.market_contract_id:
                continue
            if s.query(PaperSignal).filter_by(
                    prediction_run_id=run_id,
                    market_contract_id=c.market_contract_id).first():
                continue
            quote = s.get(MarketQuote, c.market_quote_id)
            if quote is None:
                continue
            ask = (quote.yes_ask_c or 0) / 100.0
            fee = 0.07 * ask * (1 - ask)
            net_edge = c.raw_probability - (ask + fee)
            reason = _market_gate(quote, snap, net_edge, model_approved)
            sig = PaperSignal(
                prediction_run_id=run_id,
                market_contract_id=c.market_contract_id,
                market_quote_id=c.market_quote_id,
                fixture_id=run.fixture_id, outcome_key=c.outcome_key,
                policy_version=EXEC_POLICY["version"],
                model_probability=c.raw_probability,
                ask_c=quote.yes_ask_c,
                # illustrative whole-order fee at the policy target size
                fee_c=order_fee_c(quote.yes_ask_c,
                                  EXEC_POLICY["target_contracts"]),
                net_edge=net_edge,
                decision="reject" if reason else "fill",
                reject_reason=reason, created_at=_now())
            s.add(sig)
            s.flush()
            signals += 1
            if reason:
                continue
            depth = s.query(MarketDepthLevel).filter_by(
                market_quote_id=c.market_quote_id).all()
            ladder = yes_buy_ladder(quote, depth)
            fill = simulate_fill(ladder, EXEC_POLICY["target_contracts"])
            if fill["filled"] == 0:
                sig.decision = "reject"
                sig.reject_reason = "DEPTH_INSUFFICIENT"
                continue
            # whole-order fee on the ACTUAL filled quantity, rounded up
            # once (V9 eval F8) — not a per-contract fee times count
            fee_total = order_fee_c(fill["avg_price_c"], fill["filled"])
            cost = fill["filled"] * fill["avg_price_c"] + fee_total
            # EXPOSURE gates — the central risk authority, after the fill
            # cost is known (position size / correlation / bankroll / kill)
            from src.live import risk
            risk_reason = risk.exposure_gate(
                s, fx, c.outcome_key, cost, fill["slippage_c"])
            if risk_reason:
                sig.decision = "reject"
                sig.reject_reason = risk_reason
                continue
            s.add(PaperFill(
                paper_signal_id=sig.id,
                requested_contracts=EXEC_POLICY["target_contracts"],
                filled_contracts=fill["filled"],
                avg_fill_price_c=fill["avg_price_c"],
                best_ask_c=fill["best_ask_c"],
                slippage_c=fill["slippage_c"], fee_c=fee_total,
                cost_c=cost, levels_consumed=fill["levels"],
                latency_ms=EXEC_POLICY["latency_ms"],
                reason=("partial" if fill["filled"]
                        < EXEC_POLICY["target_contracts"] else "filled"),
                created_at=_now(), status="open"))
            fills += 1
        s.commit()
        return {"signals": signals, "fills": fills}
    except Exception as exc:
        s.rollback()
        print(f"[paper] trade failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def settle_paper(fixture_id: int | None = None) -> dict:
    """Settle open paper fills for completed fixtures: a YES contract
    pays 100c per filled contract if its outcome hit, else 0. Determin-
    istic and idempotent (only touches status='open')."""
    if not plane_ready():
        return {"skipped": "dormant"}
    s = get_session()
    settled = 0
    try:
        q = (s.query(PaperFill, PaperSignal, Fixture)
             .join(PaperSignal, PaperFill.paper_signal_id == PaperSignal.id)
             .join(Fixture, PaperSignal.fixture_id == Fixture.id)
             .filter(PaperFill.status == "open",
                     Fixture.status == "post",
                     Fixture.home_goals.isnot(None)))
        for fill, sig, fx in q.all():
            if fixture_id is not None and fx.id != fixture_id:
                continue
            result = ("home_win" if fx.home_goals > fx.away_goals else
                      "away_win" if fx.away_goals > fx.home_goals
                      else "draw")
            hit = sig.outcome_key == result
            payout = fill.filled_contracts * 100 if hit else 0
            fill.outcome_hit = hit
            fill.payout_c = payout
            fill.pnl_c = payout - fill.cost_c
            fill.status = "settled"
            fill.settled_at = _now()
            settled += 1
        s.commit()
        return {"settled": settled}
    except Exception as exc:
        s.rollback()
        print(f"[paper] settle failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def paper_summary() -> dict:
    """The paper ledger P&L — settled economics + open exposure. All
    labeled PAPER; never a real position."""
    if not plane_ready():
        return {}
    s = get_session()
    try:
        fills = s.query(PaperFill).all()
        settled = [f for f in fills if f.status == "settled"]
        rejects = s.query(PaperSignal).filter_by(decision="reject").count()
        reasons: dict[str, int] = {}
        for r in (s.query(PaperSignal.reject_reason)
                  .filter_by(decision="reject").all()):
            reasons[r[0]] = reasons.get(r[0], 0) + 1
        pnl = sum(f.pnl_c or 0 for f in settled)
        cost = sum(f.cost_c or 0 for f in settled)
        return {
            "paper": True, "policy_version": EXEC_POLICY["version"],
            "fee_schedule": FEE_SCHEDULE["version"],
            "fee_basis": FEE_SCHEDULE["not_modeled"],
            "signals": s.query(PaperSignal).count(),
            "fills": len(fills),
            "rejected": rejects, "reject_reasons": reasons,
            "open_fills": sum(1 for f in fills if f.status == "open"),
            "settled_fills": len(settled),
            "settled_cost_c": cost, "settled_pnl_c": pnl,
            "roi_pct": round(100 * pnl / cost, 2) if cost else None,
            "note": ("paper execution against frozen T-10 books — never a "
                     "real order; approximate general fees (series/event "
                     "overrides + maker/taker not modeled); execution "
                     "evidence, not a track record"),
        }
    finally:
        s.close()
