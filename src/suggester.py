"""Suggestion engine.

Takes a match simulation + live Kalshi markets, computes edge/EV per market,
filters by the configured thresholds, and produces ranked TAKE/SKIP calls.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import config
from src.db import Prediction, Suggestion, SessionLocal, get_setting, utcnow
from src.kalshi_client import KalshiClient
from src.models.simulator import MatchSimulator
from src.schedule_data import Match, get_team_stats


@dataclass
class BetSuggestion:
    match_id: str
    market_id: str
    market_title: str
    outcome_key: str | None
    kickoff: str
    decimal_odds: float
    model_probability: float
    implied_probability: float
    edge: float
    expected_value: float
    confidence: float
    volume_24h: float
    recommendation: str
    reason: str


class SuggesterEngine:
    def __init__(self):
        self.kalshi = KalshiClient()
        self.simulator = MatchSimulator()

    # ------------------------------------------------------------------
    def run_for_match(self, match: Match, source: str = "scheduled",
                      is_final: bool = False) -> dict:
        """Simulate a match, price every Kalshi market on it, persist, return."""
        home_stats = get_team_stats(match.home)
        away_stats = get_team_stats(match.away)
        sim = self.simulator.simulate(home_stats, away_stats, stage=match.stage)

        markets = self._dedupe_markets(self.kalshi.get_markets_for_match(match))
        suggestions: list[BetSuggestion] = []

        with SessionLocal() as session:
            min_edge = get_setting(session, "min_edge", config.MIN_EDGE)
            min_conf = get_setting(session, "min_confidence", config.MIN_CONFIDENCE)
            min_vol = get_setting(session, "min_volume", config.MIN_VOLUME_24H)

            for mkt in markets:
                raw_model_p = self.simulator.prob_for_outcome_key(sim, mkt["outcome_key"])
                if raw_model_p is None:
                    continue
                implied_p = mkt["yes_price"]
                # Market anchoring: liquid books are usually right. Shrink the
                # model toward the market so only genuine, large disagreements
                # survive — kills the "everything is +8% value" bias.
                model_p = (config.MODEL_WEIGHT * raw_model_p
                           + (1 - config.MODEL_WEIGHT) * implied_p)
                edge = model_p - implied_p
                # EV per $1: win (1/price - 1) with prob p, lose $1 otherwise
                ev = model_p * (mkt["decimal_odds"] - 1) - (1 - model_p)

                take = (edge >= min_edge and sim["confidence"] >= min_conf
                        and mkt["volume_24h"] >= min_vol
                        and mkt["decimal_odds"] <= config.MAX_ODDS)
                reason = self._reason(edge, sim["confidence"], mkt["volume_24h"],
                                      min_edge, min_conf, min_vol,
                                      mkt["decimal_odds"])

                session.add(Prediction(
                    match_id=match.match_id, market_id=mkt["market_id"],
                    market_title=mkt["title"],
                    outcome_key=mkt.get("outcome_key"),
                    model_probability=model_p,
                    kalshi_odds=mkt["decimal_odds"], implied_probability=implied_p,
                    edge=edge, expected_value=ev, confidence=sim["confidence"],
                    xg_home=sim["xg"]["home"], xg_away=sim["xg"]["away"],
                    scoreline_json=json.dumps(sim["scorelines"]),
                    source=source, is_final=is_final,
                    model_version=sim["model_version"],
                ))

                suggestions.append(BetSuggestion(
                    match_id=match.match_id, market_id=mkt["market_id"],
                    market_title=mkt["title"],
                    outcome_key=mkt.get("outcome_key"),
                    kickoff=match.kickoff.isoformat(),
                    decimal_odds=mkt["decimal_odds"],
                    model_probability=round(model_p, 4),
                    implied_probability=round(implied_p, 4),
                    edge=round(edge, 4), expected_value=round(ev, 4),
                    confidence=sim["confidence"], volume_24h=mkt["volume_24h"],
                    recommendation="TAKE" if take else "SKIP", reason=reason,
                ))

                if take or is_final:
                    session.add(Suggestion(
                        match_id=match.match_id, market_id=mkt["market_id"],
                        market_title=mkt["title"], kickoff=match.kickoff,
                        model_probability=model_p, kalshi_odds=mkt["decimal_odds"],
                        implied_probability=implied_p, edge=edge,
                        expected_value=ev, confidence=sim["confidence"],
                        recommendation="TAKE" if take else "SKIP",
                        reason=reason, is_final=is_final,
                    ))
            session.commit()

        suggestions.sort(key=lambda s: s.expected_value, reverse=True)
        return {
            "match_id": match.match_id,
            "teams": {"home": match.home, "away": match.away},
            "kickoff": match.kickoff.isoformat(),
            "simulation": sim,
            "suggestions": [asdict(s) for s in suggestions],
            "generated_at": utcnow().isoformat(),
            "source": source,
            "is_final": is_final,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def _dedupe_markets(markets: list[dict]) -> list[dict]:
        """Collapse Kalshi contracts that represent the same real bet.

        Kalshi lists several ticker families for one outcome — e.g.
        KXWCGAME (moneyline) and KXWCMOV-...REG (regulation win) both
        resolve as "team wins in 90 minutes" and both classify to the
        same outcome_key. Keep the buyer-favorable contract per key:
        LOWEST yes_price (= highest decimal odds). Ties break to the
        higher 24h volume. Markets without an outcome_key pass through
        untouched — we can't assert two unclassified contracts are the
        same bet.
        """
        best: dict[str, dict] = {}
        passthrough: list[dict] = []
        for mkt in markets:
            key = mkt.get("outcome_key")
            if not key:
                passthrough.append(mkt)
                continue
            cur = best.get(key)
            if cur is None or (
                (mkt["yes_price"], -mkt.get("volume_24h", 0))
                < (cur["yes_price"], -cur.get("volume_24h", 0))
            ):
                best[key] = mkt
        return list(best.values()) + passthrough

    # ------------------------------------------------------------------
    @staticmethod
    def _reason(edge, conf, vol, min_edge, min_conf, min_vol, odds=0.0) -> str:
        if odds > config.MAX_ODDS:
            return f"Odds {odds:.1f} above the {config.MAX_ODDS:.0f} longshot cap"
        if edge < min_edge:
            return f"Edge {edge:+.1%} below the {min_edge:.0%} threshold"
        if conf < min_conf:
            return f"Model confidence {conf:.0%} below the {min_conf:.0%} floor"
        if vol < min_vol:
            return f"Only ${vol:,.0f} traded in 24h (need ${min_vol:,.0f})"
        return f"Model sees {edge:+.1%} of value after market anchoring"
