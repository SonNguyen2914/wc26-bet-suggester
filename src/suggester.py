"""Suggestion engine.

Takes a match simulation + live Kalshi markets, computes edge/EV per market,
filters by the configured thresholds, and produces ranked TAKE/SKIP calls.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

import config
from src import execution
from src.db import Prediction, Suggestion, SessionLocal, get_setting, utcnow
from src.kalshi_client import KalshiClient
from src.models.simulator import MatchSimulator
from src.schedule_data import Match, effective_team_stats as get_team_stats

# Families verified to list the SAME real bet (the 90-minute winner) on two
# tickers. ONLY these may collapse in dedup. Expanding this set requires
# reading Kalshi's settlement rules for the new family first — never add a
# family just because its rows LOOK like duplicates (see the
# KXWCTEAMFIRSTGOAL incident: a misclassified 1c player prop nearly
# replaced the real moneyline).
_INTERCHANGEABLE_FAMILIES = frozenset({"KXWCGAME", "KXWCMOV"})


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
    def price_live(self, match: Match, current_home: int, current_away: int,
                   minutes_elapsed: float, red_home: int = 0,
                   red_away: int = 0,
                   attack_home_mult: float = 1.0, attack_away_mult: float = 1.0,
                   defence_home_mult: float = 1.0,
                   defence_away_mult: float = 1.0,
                   phase: str = "auto",
                   markets: list[dict] | None = None,
                   first_goal_scored: bool = False) -> dict:
        """Price every current Kalshi market against a LIVE, in-progress
        state (Layer 3 manual entry). Uses simulate_remaining() — score
        seeded, time-scaled, known red cards, plus optional user-set attack
        levers for qualitative reads ("team B chasing"). The multipliers are
        the USER's transparent adjustment, applied to the raw stats before
        the sim; the response echoes them back so nothing is hidden.

        Deliberately does NOT persist and does NOT gate on edge: live edge
        vs. a market that already knows the score is informational only, not
        a signal. Never resurrect the aggressive TAKE board mid-match.
        """
        home_stats = dict(get_team_stats(match.home))
        away_stats = dict(get_team_stats(match.away))
        home_stats["attack"] = home_stats["attack"] * attack_home_mult
        away_stats["attack"] = away_stats["attack"] * attack_away_mult
        # defence stat is "how much you concede" (higher = leakier), so the
        # openness lever multiplies straight in: >1 = a more open game.
        home_stats["defence"] = home_stats["defence"] * defence_home_mult
        away_stats["defence"] = away_stats["defence"] * defence_away_mult

        sim = self.simulator.simulate_remaining(
            home_stats, away_stats, current_home, current_away,
            minutes_elapsed=minutes_elapsed, stage=match.stage,
            red_home=red_home, red_away=red_away, phase=phase)

        # The auto stream re-prices every ~30s; refetching Kalshi's ~20
        # events each cycle would be abusive, so callers may hand in a
        # recently-fetched market list.
        if markets is None:
            markets = self.kalshi.get_markets_for_match(match)
        markets = self._dedupe_markets(markets)
        # Inside extra time / penalties the 90-minute books (winner, totals,
        # margins, exact scores) are SETTLED facts — blending a settled 0/1
        # with a stale market price produces nonsense ("draw after 90: 70%").
        # Only advancement-family markets are still live, so only they price.
        in_continuation = sim["live_state"].get("phase") in ("et", "pens")
        _CONTINUATION_KEYS = {"home_advance", "away_advance",
                              "home_win_et", "away_win_et",
                              "home_win_pens", "away_win_pens"}
        # Once ANY goal has been scored, the first-goal race is a settled
        # fact — re-simulating it from the remaining match is nonsense
        # (caught live: 'Spain to score first' priced 73% while Spain had
        # already scored first). Same rule as the ET settled-books filter.
        _FIRST_GOAL_KEYS = {"home_first_goal", "away_first_goal", "no_goal"}
        rows: list[dict] = []
        for mkt in markets:
            if in_continuation and mkt.get("outcome_key") not in _CONTINUATION_KEYS:
                continue
            if first_goal_scored and mkt.get("outcome_key") in _FIRST_GOAL_KEYS:
                continue
            raw_model_p = self.simulator.prob_for_outcome_key(
                sim, mkt["outcome_key"])
            if raw_model_p is None:
                continue
            implied_p = mkt["yes_price"]
            model_p = (config.MODEL_WEIGHT * raw_model_p
                       + (1 - config.MODEL_WEIGHT) * implied_p)
            rows.append({
                "market_id": mkt["market_id"],
                "market_title": mkt["title"],
                "outcome_key": mkt.get("outcome_key"),
                "kalshi_odds": mkt["decimal_odds"],
                # what the market currently thinks vs. our live read
                "market_probability": round(implied_p, 4),
                "market_yes_bid": mkt.get("yes_bid"),
                "live_model_probability": round(model_p, 4),
                # informational only — NOT a betting signal in-play
                "difference": round(model_p - implied_p, 4),
                "volume_24h": mkt["volume_24h"],
            })

        # ---- model-first completeness -------------------------------------
        # In play, Kalshi settles and CLOSES every book the state has already
        # decided (Over 1.5 at 1-1, BTTS, impossible exact scores...), so the
        # open-market list alone shows a thin board. The live read is a MODEL
        # view: every key the remaining-match simulation prices gets a row —
        # the market columns simply stay empty where no open book exists.
        covered = {r["outcome_key"] for r in rows}
        home, away = match.home, match.away

        def _title(key: str) -> str:
            fixed = {
                "home_win": f"{home} to win (90 min)",
                "draw": "Draw after 90 min",
                "away_win": f"{away} to win (90 min)",
                "home_advance": f"{home} to advance",
                "away_advance": f"{away} to advance",
                "home_win_et": f"{home} to win in extra time",
                "away_win_et": f"{away} to win in extra time",
                "home_win_pens": f"{home} to win on penalties",
                "away_win_pens": f"{away} to win on penalties",
                "btts": "Both teams to score",
                "no_goal": "No goal in the match",
                "home_first_goal": f"{home} to score first",
                "away_first_goal": f"{away} to score first",
            }
            if key in fixed:
                return fixed[key]
            m = re.match(r"over_(\d)_5$", key)
            if m:
                return f"Over {m.group(1)}.5 total goals"
            m = re.match(r"(home|away)_margin_(\d)$", key)
            if m:
                side = home if m.group(1) == "home" else away
                return f"{side} to win by {m.group(2)}+ goals"
            m = re.match(r"score_(\d+)_(\d+)$", key)
            if m:
                h, a = m.group(1), m.group(2)
                return (f"Exact score: {h}-{a} draw" if h == a
                        else f"Exact score: {home} {h}-{a} {away}"
                        if int(h) > int(a)
                        else f"Exact score: {away} {a}-{h} {home}")
            return key

        model_keys: list[str] = []
        if not in_continuation:
            model_keys += ["home_win", "draw", "away_win"]
            model_keys += [f"over_{n}_5" for n in range(6)]
            model_keys += ["btts",
                           "home_margin_2", "home_margin_3",
                           "away_margin_2", "away_margin_3"]
            if not first_goal_scored:
                model_keys += ["home_first_goal", "away_first_goal", "no_goal"]
            model_keys += [f"score_{s['score'].replace('-', '_')}"
                           for s in sim.get("scorelines", [])[:12]]
        if match.stage == "knockout":
            model_keys += ["home_advance", "away_advance"]
            if sim.get("advance") and sim["advance"].get("home_win_et") is not None:
                model_keys += ["home_win_et", "away_win_et",
                               "home_win_pens", "away_win_pens"]
        for key in model_keys:
            if key in covered:
                continue
            p = self.simulator.prob_for_outcome_key(sim, key)
            if p is None:
                continue
            covered.add(key)
            rows.append({
                "market_id": f"model:{key}",
                "market_title": _title(key),
                "outcome_key": key,
                "kalshi_odds": None,
                "market_probability": None,
                "market_yes_bid": None,
                "live_model_probability": round(p, 4),
                "difference": None,
                "volume_24h": 0.0,
                "model_only": True,
            })

        rows.sort(key=lambda r: r["live_model_probability"], reverse=True)
        return {
            "match_id": match.match_id,
            "teams": {"home": match.home, "away": match.away},
            "stage": match.stage,
            "live_state": sim["live_state"],
            "live_outcomes": sim["outcomes"],
            "live_advance": sim.get("advance"),
            "live_confidence": sim["confidence"],
            "user_attack_levers": {"home": attack_home_mult,
                                   "away": attack_away_mult},
            "defence_levers": {"home": defence_home_mult,
                               "away": defence_away_mult},
            "markets": rows,
            "generated_at": utcnow().isoformat(),
            "disclaimer": ("Live estimate given the entered state. The market "
                           "already reflects the score; differences are "
                           "informational, not exploitable edge."),
        }

    # ------------------------------------------------------------------
    def run_for_match(self, match: Match, source: str = "scheduled",
                      is_final: bool = False) -> dict:
        """Simulate a match, price every Kalshi market on it, persist, return."""
        # Snapshot resolution BEFORE reading stats. The bracket resolver
        # mutates the Match in place, so a request that starts on a
        # placeholder slot ("NOR/ENG winner" -> _DEFAULT stats for both
        # sides) can see real names by the time markets are matched — the
        # slow first Kalshi events fetch is a seconds-wide window. That
        # exact race persisted a symmetric default-stats batch against 47
        # real SF2 markets on prod (2026-07-13, 8s after the boot prime).
        # A side never un-resolves, so resolved-at-entry means the names
        # below are stable for the whole run.
        resolved = match.fully_resolved
        home_stats = get_team_stats(match.home)
        away_stats = get_team_stats(match.away)
        sim = self.simulator.simulate(home_stats, away_stats, stage=match.stage)
        # Match-level prediction summary (identical for every market in the
        # batch): full-time W/D/L, ET/penalties advancement, and first/second-
        # half distributions. Persisted so the match page can show a forecast.
        summary_json = json.dumps({
            "full_time": sim["outcomes"],
            "advance": sim.get("advance"),
            "halves": sim.get("halves"),
        })

        # A placeholder slot prices and persists NOTHING: there are no real
        # teams behind the simulation, so any market row it wrote would be
        # default-stats noise served from cache until it went stale.
        markets = ([] if not resolved else
                   self._dedupe_markets(self.kalshi.get_markets_for_match(match)))
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
                # ALL-IN economics from the shared module (V7 evaluation
                # F5): gross edge admitted marginal trades whose true
                # edge sat under the threshold, and the displayed EV
                # ignored the entry fee the buyer actually pays.
                edge = execution.net_edge(model_p, implied_p)
                ev = execution.net_ev(model_p, implied_p)

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
                    summary_json=summary_json,
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

        ONLY families verified to be the identical bet may collapse:
        KXWCGAME (moneyline) and KXWCMOV-...REG (regulation win) both list
        the 90-minute winner. Within that pair, keep the buyer-favorable
        contract per outcome_key: LOWEST yes_price (= highest decimal
        odds), ties to higher 24h volume.

        Everything else passes through untouched even when outcome_keys
        collide — a visible duplicate is diagnosable, a silently replaced
        real price is not. (Lesson learned: a misclassified 1-cent
        KXWCTEAMFIRSTGOAL player prop once landed in home_win and dedup
        discarded the actual moneyline in its favor.)
        """
        best: dict[str, dict] = {}
        passthrough: list[dict] = []
        for mkt in markets:
            key = mkt.get("outcome_key")
            family = str(mkt.get("market_id", "")).split("-")[0].upper()
            if not key or family not in _INTERCHANGEABLE_FAMILIES:
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
