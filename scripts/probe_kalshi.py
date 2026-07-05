"""Probe Kalshi's public API for WC26 match events — run before going live.

    cd backend && source venv/bin/activate
    DEMO_MODE=false python scripts/probe_kalshi.py

Prints, per match: every relevant Kalshi event, every market inside it, and
how our classifier mapped each one. Skips known-noise families (announcer
mentions, corners, half-markets) to save requests. Also dumps the raw price
fields of the first priced market it sees, to diagnose empty order books.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DEMO_MODE", "false")

import requests

import config
from src.kalshi_client import (SKIP_FAMILIES, _classify_outcome,
                               _event_family, _events_for_match,
                               _fetch_all_open_events, _get_with_backoff,
                               _market_yes_price)
from src.schedule_data import load_schedule

PRICE_FIELDS = ("yes_bid", "yes_ask", "last_price", "no_bid", "no_ask",
                "volume", "volume_24h", "open_interest", "liquidity")


def main() -> None:
    session = requests.Session()
    session.headers["User-Agent"] = "wc26-suggester-probe/0.2"

    print(f"Fetching open events from {config.KALSHI_BASE_URL} ...")
    events = _fetch_all_open_events(session)
    print(f"Total open events on Kalshi: {len(events)}\n")

    dumped_raw = False
    for match in load_schedule():
        print("=" * 70)
        print(f"{match.home} vs {match.away}  ({match.match_id}, kickoff {match.kickoff})")
        matched = _events_for_match(events, match)
        if not matched:
            print("  !! No events matched — check team-name aliases in ALIASES")
            continue

        for ev in matched:
            family = _event_family(ev["event_ticker"])
            if family in SKIP_FAMILIES:
                print(f"  [skipped family {family}] {ev['event_ticker']} | {ev.get('title')}")
                continue

            print(f"  event: {ev['event_ticker']}  |  {ev.get('title')}")
            resp = _get_with_backoff(
                session, f"{config.KALSHI_BASE_URL}/markets",
                {"event_ticker": ev["event_ticker"], "status": "open", "limit": 100})
            for m in resp.json().get("markets", []):
                outcome = _classify_outcome(match, m, ev["event_ticker"])
                yes = _market_yes_price(m)
                label = outcome or "UNMAPPED"
                print(f"    [{label:<16}] {m['ticker']:<40} "
                      f"yes≈{yes}  vol24h={m.get('volume_24h', 0)} "
                      f"| {(m.get('title') or '')[:55]}")
                if not dumped_raw and yes is not None:
                    dumped_raw = True
                    raw = {k: m.get(k) for k in PRICE_FIELDS}
                    print(f"    --- raw price fields of first priced market: {json.dumps(raw)}")
            if not dumped_raw:
                # nothing priced yet — dump one raw market anyway for diagnosis
                mk = resp.json().get("markets", [])
                if mk:
                    raw = {k: mk[0].get(k) for k in PRICE_FIELDS}
                    print(f"    --- raw price fields (unpriced sample): {json.dumps(raw)}")
                    dumped_raw = True
            time.sleep(0.6)  # polite gap between event fetches
        print()


if __name__ == "__main__":
    main()
