"""The strategy-lab bots, each with its own strategy and temperament,
betting the REAL Kalshi books through the model. Hypothetical money only —
this is a strategy laboratory: five different philosophies running against
the same markets, so their ledgers show which instincts actually pay.

  KELLY     the quant. Pre-match only; bets model-vs-price edge >= 5pts,
            sized by half-Kelly, fee-aware. Few bets, no feelings.
  CHALK     the nerve-free favourite backer. Flat $50 on anything the model
            calls >= 65% that still pays (price <= 85c). Sleeps fine.
  MOONSHOT  the lottery hunter. $10 flyers on books <= 20c that the model
            prices >= 1.4x the market's implied. Mostly ash, sometimes gold.
  WIRE      the in-play trader. Enters on live BUY / EASY-WIN signals,
            exits on a SELL flip or +20c take-profit. Never holds through
            the anxiety when the pattern turns.
  FADE      the dip buyer. In play, buys books that crashed >= 15c from
            their pre-match price while the live model still rates them
            (>= price + 8pts), then holds to settlement. Everyone is
            overreacting.
  SWEETSPOT Son's own recipe: the model's modal exact score plus its
            neighbourhood (>=60% of the mode, max 4 books), $60 dutched
            by model probability. Patient, cluster-shaped.

Costs are modelled like the strategy page: Kalshi taker fee 0.07*P*(1-P)
per contract on entry AND on an early exit. Settlement uses the closing
snapshot's own result (yes/no), falling back to the closing price when the
snapshot was taken before Kalshi settled.
"""
from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import select

from src.db import BotPosition, MatchResult, SessionLocal, utcnow

START_BANKROLL = 1000.0

PERSONAS = {
    "KELLY": {"name": "Kelly", "emoji": "🤓",
              "tagline": "Half-Kelly on real edge, pre-match only. No feelings, no chasing.",
              "style": "value / edge"},
    "CHALK": {"name": "Chalk", "emoji": "😌",
              "tagline": "Backs the model's favourites flat. Small wins, long sleep.",
              "style": "likelihood-first"},
    "MOONSHOT": {"name": "Moonshot", "emoji": "🎰",
                 "tagline": "$10 flyers on mispriced longshots. Ash, ash, ash, gold.",
                 "style": "longshot value"},
    "WIRE": {"name": "Wire", "emoji": "⚡",
             "tagline": "Trades the live signals. In on BUY, out on SELL or +20¢.",
             "style": "in-play momentum"},
    "FADE": {"name": "Fade", "emoji": "🧊",
             "tagline": "Buys the panic when the live model disagrees. Holds. Everyone overreacts.",
             "style": "in-play contrarian"},
    "SWEETSPOT": {"name": "Sweetspot", "emoji": "🍯",
                  "tagline": "The model-weighted refinement of the Crew recipe: only the tightest cluster, dutched by probability.",
                  "style": "exact-score cluster"},
    "CREW": {"name": "The Crew", "emoji": "🤝",
             "tagline": "Son & friends' recipe v3: two-mode ladders, permanent knockout draw insurance, stakes follow belief.",
             "style": "score ladder + insurance"},
}


def fee(p: float) -> float:
    """Kalshi taker fee per contract at price p."""
    return 0.07 * p * (1.0 - p)


def bankroll(bot: str, session) -> float:
    """Cash on hand: start + realized P&L - cost locked in open positions."""
    rows = session.execute(
        select(BotPosition).where(BotPosition.bot == bot)).scalars().all()
    cash = START_BANKROLL
    for r in rows:
        if r.closed_at is None:
            cash -= r.cost
        else:
            cash += r.pnl
    return round(cash, 2)


def _has_position(bot: str, market_id: str, session) -> bool:
    return session.execute(
        select(BotPosition).where(BotPosition.bot == bot,
                                  BotPosition.market_id == market_id)
    ).scalar_one_or_none() is not None


def open_position(bot: str, match_id: str, market_id: str, title: str,
                  price: float, stake: float, note: str = "") -> dict | None:
    """Buy YES at `price` with up to `stake` dollars. One position per bot
    per market, ever — a bot's thesis on a market doesn't get do-overs."""
    if price is None or not (0.01 <= price <= 0.97):
        return None
    unit = price + fee(price)
    contracts = int(stake // unit)
    if contracts < 1:
        return None
    with SessionLocal() as s:
        if _has_position(bot, market_id, s):
            return None
        cost = round(contracts * unit, 2)
        if cost > bankroll(bot, s):
            return None
        pos = BotPosition(bot=bot, match_id=match_id, market_id=market_id,
                          market_title=title, entry_price=price,
                          contracts=contracts, cost=cost, note=note)
        s.add(pos)
        s.commit()
        print(f"[bots] {bot} BUY {contracts}x {market_id} @ {price:.2f} "
              f"(${cost}) — {note}")
        return {"bot": bot, "market_id": market_id, "contracts": contracts}


def close_position(pos_id: int, price: float, reason: str) -> None:
    """Early exit at `price` (sell YES): proceeds net of the sell-side fee."""
    with SessionLocal() as s:
        pos = s.get(BotPosition, pos_id)
        if pos is None or pos.closed_at is not None:
            return
        proceeds = pos.contracts * (price - fee(price))
        pos.closed_at = utcnow()
        pos.close_price = price
        pos.close_reason = reason
        pos.pnl = round(proceeds, 2)   # cost already deducted from bankroll
        s.commit()
        net = round(proceeds - pos.cost, 2)
        print(f"[bots] {pos.bot} EXIT {pos.market_id} @ {price:.2f} "
              f"({reason}, net {net:+})")


# ---------------------------------------------------------------------------
# Entry rules — one function per bot. `rows` are priced market dicts with
# model p / market p; live rows use the live keys. Each returns entries as
# (market_id, title, price, stake, note).
# ---------------------------------------------------------------------------

def kelly_entries(rows, cash):
    out = []
    for r in rows:
        p, c = r.get("model_probability"), r.get("implied_probability")
        if p is None or c is None or not (0.10 <= c <= 0.90):
            continue
        edge = p - c
        if edge < 0.05:
            continue
        f_star = (p - c) / (1.0 - c)         # binary Kelly at price c
        stake = min(150.0, max(0.0, cash * f_star / 2.0))
        if stake >= c + fee(c):
            out.append((r["market_id"], r["market_title"], c, stake,
                        f"edge {edge:+.2f}, half-kelly {f_star/2:.2f}"))
    return out


def chalk_entries(rows, cash):
    out = []
    for r in rows:
        p, c = r.get("model_probability"), r.get("implied_probability")
        if p is None or c is None:
            continue
        if p >= 0.65 and c <= 0.85:
            out.append((r["market_id"], r["market_title"], c, 50.0,
                        f"model {p:.0%} favourite"))
    return out


def moonshot_entries(rows, cash):
    out = []
    for r in rows:
        p, c = r.get("model_probability"), r.get("implied_probability")
        if p is None or c is None or c > 0.20 or c < 0.02:
            continue
        if p / c >= 1.4:
            out.append((r["market_id"], r["market_title"], c, 10.0,
                        f"model {p:.0%} vs {c:.0%} implied ({p/c:.1f}x)"))
    return out


def sweetspot_entries(rows, cash):
    """Son's strategy, codified: the model's modal exact score plus every
    neighbour within 60% of the mode's probability (max 4 books), $60 per
    match dutched proportional to model probability. Backtested +6.5% over
    the four archived knockouts (both QFs hit the 1-1 cluster at ~2x; both
    SFs missed just outside it) — the arena settles whether that holds."""
    import re as _re
    scores = []
    for r in rows:
        m = _re.match(r"score_(\d+)_(\d+)$", r.get("outcome_key") or "")
        p, c = r.get("model_probability"), r.get("implied_probability")
        if m and p and c and c > 0:
            scores.append((r, p, c))
    if not scores:
        return []
    scores.sort(key=lambda x: -x[1])
    mode_p = scores[0][1]
    cluster = [x for x in scores if x[1] >= 0.6 * mode_p][:4]
    psum = sum(p for _, p, _ in cluster) or 1.0
    out = []
    for r, p, c in cluster:
        stake = 60.0 * p / psum
        out.append((r["market_id"], r["market_title"], c, stake,
                    f"cluster {p:.0%} model vs {c:.0%} implied"))
    return out


# Son's crew, two modes — v3 carries the three evidence-backed upgrades
# from the 2026-07-16 review: (1) knockout draw insurance is PERMANENT
# (both backtest losses were 1-1 at 90; underdogs play for pens), plus 0-0
# when the model reads a cagey game; (2) stakes follow BELIEF (model
# probability), not an even split — most of the cluster bot's edge was
# sizing; (3) the mismatch ladder keeps one 1-1 rung in knockouts
# (ARG-SUI: ten men parked the bus to 1-1 anyway).
CREW_EVEN_LADDER = ["score_1_0", "score_0_1", "score_2_0", "score_0_2",
                    "score_2_1", "score_1_2"]
CREW_STRONG_HOME = ["score_2_0", "score_2_1", "score_3_0", "score_3_1",
                    "score_3_2"]
CREW_STRONG_AWAY = ["score_0_2", "score_1_2", "score_0_3", "score_1_3",
                    "score_2_3"]
CREW_UNEVEN_GAP = 0.20       # win90 gap that makes a game "so un-even"
CREW_DRAW_TRIGGER = 0.25     # group-stage insurance stays feel-based; 2-2 always is
CREW_ZERO_ZERO_MIN = 0.06    # model P(0-0) that reads "two good defences"


def crew_entries(rows, cash, stage="knockout"):
    """Son & friends' recipe, v3. Judge the game first: roughly even ->
    the tight two-goal ladder both ways; clearly un-even -> the stronger
    side's wins up to 3. Knockouts always carry 1-1 (draws at 90 killed
    every backtest loss) and add 0-0 when the model calls the game cagey;
    2-2 stays a judgment call (draw read >= trigger). $60 per match,
    staked proportional to the model's belief in each rung."""
    by_key = {r.get("outcome_key"): r for r in rows}
    hw = (by_key.get("home_win") or {}).get("model_probability") or 0
    aw = (by_key.get("away_win") or {}).get("model_probability") or 0
    draw_p = (by_key.get("draw") or {}).get("model_probability") or 0
    knockout = stage == "knockout"

    if abs(hw - aw) >= CREW_UNEVEN_GAP:
        want = list(CREW_STRONG_HOME if hw > aw else CREW_STRONG_AWAY)
        mode = "strong-side ladder"
        if knockout:
            want.append("score_1_1")        # the parked-bus hedge
    else:
        want = list(CREW_EVEN_LADDER)
        mode = "even ladder"
        if knockout or draw_p >= CREW_DRAW_TRIGGER:
            want.append("score_1_1")
        if draw_p >= CREW_DRAW_TRIGGER:
            want.append("score_2_2")
    zz = by_key.get("score_0_0")
    if (knockout and zz
            and (zz.get("model_probability") or 0) >= CREW_ZERO_ZERO_MIN):
        want.append("score_0_0")

    picked = []
    for k in dict.fromkeys(want):           # de-dupe, keep order
        r = by_key.get(k)
        if r and (r.get("implied_probability") or 0) > 0 \
                and (r.get("model_probability") or 0) > 0:
            picked.append(r)
    if not picked:
        return []
    psum = sum(r["model_probability"] for r in picked)
    out = []
    for r in picked:
        stake = 60.0 * r["model_probability"] / psum
        tag = mode
        if r["outcome_key"] in ("score_1_1", "score_2_2", "score_0_0"):
            tag += " + insurance"
        out.append((r["market_id"], r["market_title"],
                    r["implied_probability"], stake, tag))
    return out


def wire_entries(signals, cash):
    """Fresh BUY-side live signals (watched BUY or easy_win) -> enter."""
    out = []
    for sg in signals:
        if sg["side"] != "BUY":
            continue
        c = sg.get("market_probability")
        if c is None:
            continue
        out.append((sg["market_id"], sg["market_title"], c, 40.0,
                    f"{sg['kind']} signal @ {sg.get('minute') or '?'}'"))
    return out


def fade_entries(live_rows, ref_prices, cash):
    out = []
    for r in live_rows:
        c = r.get("market_probability")
        p = r.get("live_model_probability")
        ref = ref_prices.get(r["market_id"])
        if c is None or p is None or ref is None:
            continue
        if ref - c >= 0.15 and p - c >= 0.08:
            out.append((r["market_id"], r["market_title"], c, 60.0,
                        f"crashed {ref:.2f}->{c:.2f}, live model {p:.0%}"))
    return out


def wire_exits(open_positions, live_rows, sell_signal_ids):
    """(pos, price, reason) for WIRE's exit rules: SELL flip or +20c."""
    price_by_id = {r["market_id"]: r.get("market_probability")
                   for r in live_rows}
    out = []
    for pos in open_positions:
        cur = price_by_id.get(pos.market_id)
        if cur is None:
            continue
        if pos.market_id in sell_signal_ids:
            out.append((pos, cur, "sell signal"))
        elif cur - pos.entry_price >= 0.20:
            out.append((pos, cur, "take profit +20c"))
    return out


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------

def settle_match(match_id: str) -> int:
    """Settle every open position on a finished match from the closing
    snapshot: Kalshi's own result when present, closing price heuristic
    when the snapshot pre-dates settlement. Positions with no readable
    outcome stay open for the next pass (snapshots backfill)."""
    import json as _json
    from src.db import MarketClosing
    settled = 0
    with SessionLocal() as s:
        open_pos = s.execute(
            select(BotPosition).where(BotPosition.match_id == match_id,
                                      BotPosition.closed_at.is_(None))
        ).scalars().all()
        if not open_pos:
            return 0
        closings = s.execute(
            select(MarketClosing).where(MarketClosing.match_id == match_id)
        ).scalars().all()
        by_market: dict[str, dict] = {}
        for c in closings:
            try:
                by_market[c.market_id] = _json.loads(c.data_json or "{}")
            except Exception:
                continue
        for pos in open_pos:
            data = by_market.get(pos.market_id)
            if not data:
                continue
            result = (data.get("result") or "").lower()
            if result not in ("yes", "no"):
                try:
                    last = float(data.get("last_price") or "")
                except (TypeError, ValueError):
                    continue
                if last >= 0.95:
                    result = "yes"
                elif last <= 0.05:
                    result = "no"
                else:
                    continue                    # genuinely unreadable — wait
            pos.closed_at = utcnow()
            pos.close_price = 1.0 if result == "yes" else 0.0
            pos.close_reason = f"settled {result}"
            pos.pnl = round(pos.contracts * 1.0, 2) if result == "yes" else 0.0
            settled += 1
            net = round(pos.pnl - pos.cost, 2)
            print(f"[bots] {pos.bot} SETTLED {result.upper()} "
                  f"{pos.market_id} (net {net:+})")
        s.commit()
    return settled


# ---------------------------------------------------------------------------
# The tick — one pass over every relevant match
# ---------------------------------------------------------------------------

def bots_tick(engine) -> dict:
    """Entries, exits and settlements for all five bots. Cheap: reuses the
    cached prediction batch pre-match, the cached live_auto cycle in play,
    and the LiveSignal rows the signal job already wrote."""
    from src.cache import latest_for_match
    from src.db import LiveSignal, MatchLiveSnapshot
    from src.live_auto import live_auto
    from src.schedule_data import is_trackable, load_schedule
    import config

    now = utcnow()
    opened = closed = settled = 0
    with SessionLocal() as s:
        live_ids = set(s.execute(
            select(MatchLiveSnapshot.match_id)).scalars())
        frozen_ids = set(s.execute(select(MatchResult.match_id)).scalars())

    for m in load_schedule():
        # -- settle finished matches with open positions -------------------
        if m.match_id in frozen_ids:
            settled += settle_match(m.match_id)
            continue

        trackable = is_trackable(m, now, config.HOURLY_PREDICTION_WINDOW_HOURS,
                                 config.TRACK_HOURS_AFTER_KICKOFF)
        if not trackable or not m.fully_resolved:
            continue

        if m.match_id in live_ids:
            # ---- in play: WIRE + FADE ------------------------------------
            out = live_auto(m, engine,
                            (latest_for_match(m.match_id) or {}).get("xg"))
            if not out.get("available"):
                continue
            live_rows = [r for r in out.get("markets", [])
                         if r.get("market_probability") is not None]
            with SessionLocal() as s:
                fresh = s.execute(
                    select(LiveSignal)
                    .where(LiveSignal.match_id == m.match_id,
                           LiveSignal.created_at >= now - timedelta(seconds=90))
                ).scalars().all()
                sigs = [{"market_id": g.market_id, "market_title": g.market_title,
                         "side": g.side, "kind": g.kind or "watched",
                         "market_probability": g.market_probability,
                         "minute": g.minute} for g in fresh]
                sell_ids = {g["market_id"] for g in sigs if g["side"] == "SELL"}
                wire_open = s.execute(
                    select(BotPosition).where(BotPosition.bot == "WIRE",
                                              BotPosition.match_id == m.match_id,
                                              BotPosition.closed_at.is_(None))
                ).scalars().all()
                cash_wire = bankroll("WIRE", s)
                cash_fade = bankroll("FADE", s)

            for pos, price, reason in wire_exits(wire_open, live_rows, sell_ids):
                close_position(pos.id, price, reason)
                closed += 1
            for mk, title, price, stake, note in wire_entries(sigs, cash_wire):
                if open_position("WIRE", m.match_id, mk, title, price,
                                 stake, note):
                    opened += 1
            # FADE's reference: the T-10 locked pre-match price
            lock = latest_for_match(m.match_id, final_only=True)
            refs = {r["market_id"]: r.get("implied_probability")
                    for r in (lock or {}).get("markets", [])}
            for mk, title, price, stake, note in fade_entries(
                    live_rows, refs, cash_fade):
                if open_position("FADE", m.match_id, mk, title, price,
                                 stake, note):
                    opened += 1
        else:
            # ---- pre-match: KELLY / CHALK / MOONSHOT ---------------------
            batch = latest_for_match(m.match_id)
            if not batch:
                continue
            rows = [r for r in batch.get("markets", [])
                    if r.get("implied_probability") is not None]
            with SessionLocal() as s:
                cash = {b: bankroll(b, s)
                        for b in ("KELLY", "CHALK", "MOONSHOT", "SWEETSPOT",
                                  "CREW")}
            for bot, entries in (("KELLY", kelly_entries(rows, cash["KELLY"])),
                                 ("CHALK", chalk_entries(rows, cash["CHALK"])),
                                 ("MOONSHOT", moonshot_entries(rows, cash["MOONSHOT"])),
                                 ("SWEETSPOT", sweetspot_entries(rows, cash["SWEETSPOT"])),
                                 ("CREW", crew_entries(rows, cash["CREW"], stage=m.stage))):
                for mk, title, price, stake, note in entries:
                    if open_position(bot, m.match_id, mk, title, price,
                                     stake, note):
                        opened += 1

    return {"opened": opened, "closed": closed, "settled": settled}
