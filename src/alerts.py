"""Discord webhook alerts. No-ops silently when the webhook isn't configured."""
from __future__ import annotations

import requests

import config


def send_discord(message: str) -> bool:
    if not config.DISCORD_WEBHOOK_URL:
        print(f"[alert skipped - no webhook] {message}")
        return False
    try:
        resp = requests.post(config.DISCORD_WEBHOOK_URL,
                             json={"content": message[:1900]}, timeout=8)
        return resp.status_code in (200, 204)
    except requests.RequestException as exc:
        print(f"[alert failed] {exc}")
        return False


def alert_new_take(match_name: str, market_title: str, edge: float, ev: float) -> None:
    send_discord(
        f"🟢 **NEW VALUE BET** — {match_name}\n"
        f"{market_title}\nEdge: {edge:+.1%} | EV per $1: {ev:+.2f}"
    )


def alert_ripe(match_name: str, market_title: str, timing: dict) -> None:
    reasons = "\n".join(f"• {r}" for r in timing["reasons"][:4])
    send_discord(
        f"⏰ **RIPE — BET WINDOW OPEN** ({timing['score']:.0f}/100) — {match_name}\n"
        f"**{market_title}** @ {timing['current_odds']:.2f} "
        f"(edge {timing['current_edge']:+.1%})\n{reasons}"
    )


def alert_final_lock(match_name: str, best_bet: dict | None) -> None:
    if best_bet:
        send_discord(
            f"🔴 **FINAL DECISION LOCKED** (T-10min) — {match_name}\n"
            f"Best bet: {best_bet['market_title']}\n"
            f"Model: {best_bet['model_probability']:.0%} | "
            f"Odds: {best_bet['decimal_odds']} | "
            f"EV: {best_bet['expected_value']:+.2f} → **{best_bet['recommendation']}**"
        )
    else:
        send_discord(f"🔴 **FINAL DECISION LOCKED** — {match_name}: no bets clear the bar. SKIP all.")
