"""FastAPI app — the on-demand layer.

Endpoints
  GET  /api/health
  GET  /api/matches/upcoming?hours_ahead=48
  GET  /api/suggestions                      likelihood ranking board (tiered)
  GET  /api/prediction/{match_id}            cached (or fresh if stale/missing)
  GET  /api/prediction/{match_id}?force_refresh=true
  GET  /api/prediction/{match_id}/timeline   how one outcome evolved
  POST /api/prediction/{match_id}/refresh    force a fresh run (one match)
  POST /api/prediction/{match_id}/live       price markets vs a live state
  GET  /api/prediction/{match_id}/live-state  auto-fetch live state (feed)
  GET  /api/live-feed/budget                  API-Football calls remaining today
  POST /api/refresh-all                      force fresh runs (all trackable)
  GET  /api/settings                         current thresholds
  POST /api/settings                         update thresholds
"""
from __future__ import annotations

import time
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select

import config
from src.cache import latest_for_match, timeline_for_match
from src.db import (SessionLocal, get_setting, init_db,
                    set_setting, utcnow)
from src.live_feed import budget_status, live_state_for
from src.model_cache import refresh_model_cache
from src.schedule_data import (get_match, get_team_stats, has_sourced_stats,
                               is_trackable, load_schedule, provisional_teams)
from src.suggester import SuggesterEngine
from src import spike_detector
from src import live_state as live_state_svc
from src.bracket import bracket_status

app = FastAPI(title="Kalshi WC26 Bet Suggester", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = SuggesterEngine()


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "demo_mode": config.DEMO_MODE, "time": utcnow().isoformat()}


@app.get("/api/matches/upcoming")
def upcoming_matches(hours_ahead: int = Query(48, ge=1, le=720)):
    now = utcnow()
    horizon = now + timedelta(hours=hours_ahead)
    out = []
    for m in load_schedule():
        if not (now < m.kickoff <= horizon):
            continue
        cached = latest_for_match(m.match_id)
        # A QF slot whose feeder hasn't finished yet is a placeholder ("USA/BEL
        # winner"); the UI shows it as TBD and skips the prediction link. A
        # resolved team with no sourced TEAM_STATS runs on _DEFAULT — flagged
        # provisional so the model's humility is visible, never hidden.
        tbd = not m.fully_resolved
        prov = [t for t in (m.home, m.away)
                if (t == m.home and m.home_resolved or
                    t == m.away and m.away_resolved)
                and not has_sourced_stats(t)]
        out.append({
            "match_id": m.match_id,
            "home": m.home,
            "away": m.away,
            "group": m.group,
            "stage": m.stage,
            "venue": m.venue,
            "kickoff": m.kickoff.isoformat(),
            "seconds_to_kickoff": int((m.kickoff - now).total_seconds()),
            "has_prediction": cached is not None,
            "is_final": bool(cached and cached["is_final"]),
            "confidence": cached["confidence"] if cached else None,
            "tbd": tbd,
            "home_resolved": m.home_resolved,
            "away_resolved": m.away_resolved,
            "provisional_stats": prov,
        })
    out.sort(key=lambda x: x["seconds_to_kickoff"])
    return {"matches": out, "generated_at": now.isoformat()}


@app.get("/api/suggestions")
def suggestions(limit: int = Query(50, ge=1, le=200)):
    """Ranking board: every market on every trackable match, filtered by
    LIKELIHOOD only — edge is displayed, never a gate — sorted most-likely
    first with a deterministic tiebreak (likelihood ↓, edge ↓, kickoff ↑).

    Tier 1 keeps markets at/above SUGGEST_PRIMARY_FLOOR (49%). If nothing
    across ALL matches clears it, tier 2 falls back to SUGGEST_FALLBACK_FLOOR
    (40%). If even that is empty, the board is honestly empty: tier_used is
    null so the frontend can say so instead of pretending. No per-match cap —
    one match may contribute many rows. TAKE/alert logic stays edge-based
    elsewhere; this endpoint is purely the likelihood board.
    """
    now = utcnow()
    pool: list[dict] = []
    for m in load_schedule():
        if not is_trackable(m, now, config.HOURLY_PREDICTION_WINDOW_HOURS,
                            config.TRACK_HOURS_AFTER_KICKOFF):
            continue
        # Drop a match's bets the INSTANT it ends (a frozen result exists),
        # not 4h later — separate from the scoreboard's FT grace window.
        if live_state_svc.is_finished(m.match_id):
            continue
        snap = latest_for_match(m.match_id)
        if not snap:
            continue
        for mkt in snap["markets"]:
            pool.append({
                "match_id": m.match_id,
                "home": m.home,
                "away": m.away,
                "market_id": mkt["market_id"],
                "market_title": mkt["market_title"],
                "outcome_key": mkt.get("outcome_key"),
                "kickoff": m.kickoff.isoformat(),
                "kalshi_odds": mkt["kalshi_odds"],
                "model_probability": mkt["model_probability"],
                "implied_probability": mkt["implied_probability"],
                "edge": mkt["edge"],
                "expected_value": mkt["expected_value"],
                "confidence": snap["confidence"],
                "is_final": snap["is_final"],
            })

    tier_used = None
    floor = config.SUGGEST_PRIMARY_FLOOR
    board = [s for s in pool if s["model_probability"] >= floor]
    if board:
        tier_used = int(round(floor * 100))
    else:
        floor = config.SUGGEST_FALLBACK_FLOOR
        board = [s for s in pool if s["model_probability"] >= floor]
        if board:
            tier_used = int(round(floor * 100))

    board.sort(key=lambda s: (-s["model_probability"], -s["edge"], s["kickoff"]))
    return {"suggestions": board[:limit], "tier_used": tier_used,
            "generated_at": now.isoformat()}


@app.get("/api/prediction/{match_id}")
def get_prediction(match_id: str, force_refresh: bool = False):
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")

    # A finished match's page is a REVIEW page: never re-simulate (fresh
    # runs see only settled books and would blank the table). Serve the
    # T-10 LOCKED batch — the model's committed pre-kickoff numbers, kept
    # for exactly this "was my model any good?" check — else the last
    # cached batch. force_refresh is deliberately ignored here: a tapped
    # Refresh button must never wipe the review view.
    if live_state_svc.is_finished(match_id):
        locked = latest_for_match(match_id, final_only=True)
        if locked and locked["markets"]:
            return {"freshness": "locked", **locked, "is_stale": False}
        cached = latest_for_match(match_id)
        if cached and cached["markets"]:
            return {"freshness": "cached", **cached, "is_stale": False}
        # nothing survived (pre-persistence wipe): summary from a cheap
        # market-less sim, honestly empty markets, zero Kalshi calls
        sim = engine.simulator.simulate(
            get_team_stats(match.home), get_team_stats(match.away),
            stage=match.stage)
        return {"freshness": "cached", "match_id": match_id,
                "generated_at": utcnow().isoformat(), "age_seconds": 0,
                "is_stale": False, "source": "post_match", "is_final": False,
                "xg": sim["xg"], "scorelines": sim["scorelines"],
                "summary": {"full_time": sim["outcomes"],
                            "advance": sim.get("advance"),
                            "halves": sim.get("halves")},
                "confidence": sim["confidence"], "markets": [],
                "suggestions": []}

    if not force_refresh:
        cached = latest_for_match(match_id)
        if cached and not cached["is_stale"]:
            return {"freshness": "cached", **cached}

    t0 = time.time()
    result = engine.run_for_match(match, source="on_demand")
    refresh_model_cache(result)   # keep the ripeness poller's edge current
    fresh = latest_for_match(match_id)
    if fresh is None:
        # Zero priceable Kalshi markets (e.g. a bracket slot still carrying
        # a placeholder side) persists zero Prediction rows — serve the
        # simulation honestly with an empty markets list, never a 500.
        sim = result["simulation"]
        fresh = {
            "match_id": match_id,
            "generated_at": result["generated_at"],
            "age_seconds": 0,
            "is_stale": False,
            "source": result["source"],
            "is_final": result["is_final"],
            "xg": sim["xg"],
            "scorelines": sim["scorelines"],
            "summary": {"full_time": sim["outcomes"],
                        "advance": sim.get("advance"),
                        "halves": sim.get("halves")},
            "confidence": sim["confidence"],
            "markets": [],
        }
    return {
        "freshness": "fresh",
        "inference_time_ms": round((time.time() - t0) * 1000),
        "suggestions": result["suggestions"],
        **fresh,
    }


class LiveStateIn(BaseModel):
    current_home: int = 0
    current_away: int = 0
    minutes_elapsed: float = 0.0
    # red cards as COUNTS (0-3 per side); legacy booleans coerce (True -> 1)
    red_home: int = 0
    red_away: int = 0
    # match segment: auto | regulation | et | pens. "auto" infers from the
    # minute (>90 in a knockout = extra time).
    phase: str = "auto"
    # user-set attack levers for qualitative reads (1.0 = no adjustment)
    attack_home_mult: float = 1.0
    attack_away_mult: float = 1.0


@app.post("/api/prediction/{match_id}/live")
def live_prediction(match_id: str, state: LiveStateIn):
    """Layer 3: price current markets against a manually-entered live state.
    Ephemeral (not persisted), edge-ungated, honestly framed — see
    SuggesterEngine.price_live()."""
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    if state.current_home < 0 or state.current_away < 0:
        raise HTTPException(422, "score cannot be negative")
    if not (0 <= state.minutes_elapsed <= 130):
        raise HTTPException(422, "minutes_elapsed out of range")
    if state.phase not in ("auto", "regulation", "et", "pens"):
        raise HTTPException(422, "phase must be auto|regulation|et|pens")
    if state.phase in ("et", "pens") and match.stage != "knockout":
        raise HTTPException(422, "extra time/penalties only exist in knockouts")
    for r in (state.red_home, state.red_away):
        if not (0 <= r <= 3):
            raise HTTPException(422, "red cards out of range (0-3)")
    for m in (state.attack_home_mult, state.attack_away_mult):
        if not (0.25 <= m <= 3.0):
            raise HTTPException(422, "attack lever out of range (0.25-3.0)")
    try:
        return engine.price_live(
            match, state.current_home, state.current_away,
            state.minutes_elapsed, state.red_home, state.red_away,
            state.attack_home_mult, state.attack_away_mult,
            phase=state.phase)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/api/prediction/{match_id}/live-state")
def fetch_live_state(match_id: str):
    """Layer 2: auto-fetch the real current state (score/minute/red cards)
    for a match from API-Football, so the live panel can pre-fill instead of
    the user typing it. Returns {available: false, ...} (never an error) when
    the feed is unconfigured, over budget, or the match isn't live — the UI
    then falls back to manual entry."""
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    state = live_state_for(match.home, match.away)
    if state is None:
        return {"available": False, "match_id": match_id,
                "budget": budget_status(),
                "reason": ("feed not configured" if not config.API_FOOTBALL_KEY
                           else "no live match found or feed unavailable")}
    from src.live_auto import sim_minutes
    return {
        "available": True,
        "match_id": match_id,
        "current_home": state["home_goals"],
        "current_away": state["away_goals"],
        # match PROGRESS, not the wall clock: 1H stoppage clamps to 45'
        # so a manual simulation doesn't eat the second half's budget
        "minutes_elapsed": sim_minutes(
            float(state["minutes_elapsed"] or 0.0),
            state.get("status_short") or ""),
        "red_home": state["red_home"],
        "red_away": state["red_away"],
        "status_short": state["status_short"],
        "is_live": state["is_live"],
        "is_finished": state["is_finished"],
        "goals_list": state.get("goals_list", []),
        "budget": budget_status(),
    }


@app.get("/api/live-scores")
def live_scores():
    """Live scoreboard for the landing page. Served from the live-state
    snapshot store (refreshed every poll), NOT directly from the feed — so a
    match in a between-periods break (90'->ET, ET->penalties) doesn't vanish,
    and just-finished matches show as FT cards for a grace window. Costs no
    feed call itself (the poller does the fetching)."""
    return {"live": live_state_svc.scoreboard_entries(),
            "budget": budget_status(),
            "generated_at": utcnow().isoformat()}


@app.get("/api/past-matches")
def past_matches():
    """Finished matches, most-recent first, for the Past matches section."""
    return {"past": live_state_svc.past_matches(),
            "generated_at": utcnow().isoformat()}


@app.get("/api/team-info/{match_id}")
def team_info(match_id: str):
    """Both teams' scouting blurbs + headline stats for the match page's
    "How they play" cards. A READ AID for the bettor — these blurbs never
    touch probabilities. Team names resolved from the schedule; a placeholder
    QF slot returns empty blurbs until the bracket fills in."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")

    def blurb(team: str, resolved: bool) -> dict:
        if not resolved:
            return {"team": team, "scouting": "", "resolved": False,
                    "provisional": False}
        s = get_team_stats(team)
        return {
            "team": team,
            "scouting": s.get("scouting", ""),
            "resolved": True,
            "provisional": not has_sourced_stats(team),
            "attack": s["attack"], "defence": s["defence"],
            "form": s["form"], "fatigue": s["fatigue"],
        }

    return {
        "match_id": match_id,
        "home": blurb(m.home, m.home_resolved),
        "away": blurb(m.away, m.away_resolved),
    }


@app.get("/api/prediction/{match_id}/live-auto")
def live_auto_stream(match_id: str):
    """The self-running live read: snapshot state + live shot stats ->
    derived attack levers -> rest-of-match simulation -> every open market
    priced. Server-cached ~25s so any number of viewers costs one cycle.
    Informational only, never a TAKE signal — the market knows the score."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    from src.live_auto import live_auto as _run
    cached = latest_for_match(match_id)
    return {"match_id": match_id,
            **_run(m, engine, (cached or {}).get("xg"))}


@app.get("/api/live-stats/{match_id}")
def live_match_stats(match_id: str):
    """Broadcast-style team stat rows (possession, shots, corners...) for a
    live or just-finished match, from ESPN's keyless boxscore. Cached 30s
    server-side; no feed budget."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    from src.live_feed import espn_match_stats
    return {"match_id": match_id, "home_team": m.home, "away_team": m.away,
            **espn_match_stats(m.home, m.away)}


@app.get("/api/team-news/{match_id}")
def team_news(match_id: str):
    """Matchday lineups (FACTS: starters / bench), from ESPN's keyless
    summary — typically posted ~1h before kickoff. Never a model input
    beyond settled-fact effects (an out-of-squad player can't score)."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    from src.live_feed import espn_lineups
    lu = espn_lineups(m.home, m.away)
    return {"match_id": match_id, "home_team": m.home, "away_team": m.away,
            "kickoff": m.kickoff.isoformat(), "venue": m.venue, **lu}


@app.get("/api/research/{match_id}")
def research_bundle(match_id: str):
    """The research record for one match, three aligned views per market:
    the T-10 LOCKED model numbers, the market's CLOSING/settlement state,
    and the frozen result. Closing rows exist once the post-FT snapshot
    has been captured (automatic at freeze; POST .../snapshot to backfill)."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    import json as _json

    from sqlalchemy import select as _select

    from src.db import MatchResult, OddsReading, Prediction
    from src.research import closing_rows

    with SessionLocal() as s:
        res = s.get(MatchResult, match_id)
        result = None if res is None else {
            "home_goals": res.home_goals, "away_goals": res.away_goals,
            "status_short": res.status_short,
            "finished_at": res.finished_at.isoformat() if res.finished_at else None,
            "goals": _json.loads(res.goals_json or "[]"),
        }
        # T-10 lock: newest is_final row per market
        locked = s.execute(
            _select(Prediction)
            .where(Prediction.match_id == match_id, Prediction.is_final)
            .order_by(Prediction.created_at.desc())
        ).scalars().all()
        seen: set[str] = set()
        final_lock = []
        for r in locked:
            if r.market_id in seen:
                continue
            seen.add(r.market_id)
            final_lock.append({
                "market_id": r.market_id, "market_title": r.market_title,
                "outcome_key": r.outcome_key,
                "model_probability": r.model_probability,
                "kalshi_odds": r.kalshi_odds,
                "implied_probability": r.implied_probability,
                "edge": r.edge, "confidence": r.confidence,
                "locked_at": r.created_at.isoformat() if r.created_at else None,
            })
        # last traded reading per market (the true pre-settlement close)
        reads = s.execute(
            _select(OddsReading)
            .where(OddsReading.match_id == match_id)
            .order_by(OddsReading.created_at.desc())
        ).scalars().all()
        rseen: set[str] = set()
        last_readings = []
        for r in reads:
            if r.market_id in rseen:
                continue
            rseen.add(r.market_id)
            last_readings.append({
                "market_id": r.market_id, "yes_price": r.yes_price,
                "model_probability": r.model_probability, "edge": r.edge,
                "read_at": r.created_at.isoformat() if r.created_at else None,
            })
    return {"match_id": match_id, "home_team": m.home, "away_team": m.away,
            "result": result, "final_lock": final_lock,
            "closing": closing_rows(match_id),
            "last_readings": last_readings,
            "generated_at": utcnow().isoformat()}


@app.post("/api/research/{match_id}/snapshot")
def research_capture(match_id: str):
    """Capture (or backfill) the closing-market snapshot for a match.
    Idempotent — a match already snapshotted reports 'exists'. Works after
    settlement too: Kalshi keeps settled markets queryable by event."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    from src.research import capture_closing_snapshot
    return {"match_id": match_id, **capture_closing_snapshot(m)}


@app.get("/api/reference-odds/{match_id}")
def get_reference_odds(match_id: str):
    """Sportsbook reference odds (API-Football, display-only). Fills the
    gap while Kalshi hasn't listed a family yet (e.g. Correct Score opens
    1-2 days out). NEVER feeds the board, the strategy engine, or any
    edge gate — see src/reference_odds.py for the ground rules."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    from src.reference_odds import reference_odds
    return reference_odds(m, latest_for_match(match_id))


@app.get("/api/player-props/{match_id}")
def player_props(match_id: str):
    """Per-player anytime / first-goalscorer probabilities for a match —
    Poisson thinning of the match sim's team xG by each player's FIFA-PDF
    scoring share (see src/player_props.py for the math + honest limits).
    Model estimates only: Kalshi's player markets stay unpriced until their
    settlement rules are verified."""
    m = get_match(match_id)
    if not m:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    if not m.fully_resolved:
        return {"available": False, "match_id": match_id,
                "reason": "bracket not resolved"}
    snap = latest_for_match(match_id)
    if snap:
        xgh, xga = snap["xg"]["home"], snap["xg"]["away"]
    else:  # no cached sim yet — derive from the same xG model directly
        from src.models.xg_model import predict_xg
        xgh, xga = predict_xg(get_team_stats(m.home), get_team_stats(m.away))
    from src.player_props import props_for, join_markets, join_match_markets
    props = props_for(m.home, m.away, m.stage, xgh, xga)
    join_markets(m.home, props["home"])     # tournament-anytime + Kalshi rows
    join_markets(m.away, props["away"])
    join_match_markets(m.home, m.away, props)   # per-match 1+/2+/3+ + assists
    from src.live_feed import espn_lineups
    from src.player_props import apply_lineups
    apply_lineups(props, espn_lineups(m.home, m.away))  # facts-only squad status
    return {
        "available": True,
        "match_id": match_id,
        "home_team": m.home, "away_team": m.away,
        "stage": m.stage,
        **props,
        "generated_at": utcnow().isoformat(),
        "disclaimer": ("Model estimates from 5-match FIFA data. Minutes and "
                       "line-ups are not modelled; a substitute's share "
                       "reflects his tournament so far. Kalshi player "
                       "markets are not priced against these numbers."),
    }


@app.get("/api/bracket")
def bracket():
    """Current knockout bracket: which QF sides are known vs still placeholders,
    plus the list of resolved teams running on provisional (unsourced) stats.
    Read-only — the resolver job does the feed work on its own schedule."""
    status = bracket_status()
    status["provisional_teams"] = provisional_teams()
    status["generated_at"] = utcnow().isoformat()
    return status


@app.get("/api/live-feed/budget")
def live_feed_budget():
    """How many API-Football calls remain today (transparency + debugging)."""
    return budget_status()


@app.get("/api/spike-detector/state")
def spike_detector_state():
    """LOG-ONLY Layer 1: the scoreline the detector currently infers per
    trackable match, from Kalshi's score markets. Read-only, drives nothing —
    it's here so the detector can be eyeballed live while its thresholds are
    still being tuned."""
    now = utcnow()
    out = []
    for m in load_schedule():
        if not is_trackable(m, now, config.HOURLY_PREDICTION_WINDOW_HOURS,
                            config.TRACK_HOURS_AFTER_KICKOFF):
            continue
        leader = spike_detector.current_leader(m.match_id)
        out.append({
            "match_id": m.match_id,
            "inferred_score": f"{leader[0]}-{leader[1]}" if leader else None,
        })
    return {"matches": out, "note": "log-only; does not affect predictions"}


@app.get("/api/prediction/{match_id}/timeline")
def prediction_timeline(match_id: str, outcome_key: str = "home_win"):
    if not get_match(match_id):
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    points = timeline_for_match(match_id, outcome_key=outcome_key)
    return {"match_id": match_id, "outcome_key": outcome_key,
            "points": points, "count": len(points)}


@app.post("/api/prediction/{match_id}/refresh")
def refresh_prediction(match_id: str):
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    result = engine.run_for_match(match, source="on_demand")
    refresh_model_cache(result)   # keep the ripeness poller's edge current
    return {"status": "refreshed", "match_id": match_id,
            "suggestions": result["suggestions"],
            "generated_at": result["generated_at"]}


@app.post("/api/refresh-all")
def refresh_all():
    """Force a fresh simulation + live Kalshi prices for every trackable
    match. One failing match never blocks the rest: it lands in `failed`
    and the loop continues, so the response always says exactly which
    matches are current and which are showing last-known data."""
    now = utcnow()
    t0 = time.time()
    refreshed: list[str] = []
    failed: list[str] = []
    for m in load_schedule():
        if not is_trackable(m, now, config.HOURLY_PREDICTION_WINDOW_HOURS,
                            config.TRACK_HOURS_AFTER_KICKOFF):
            continue
        try:
            result = engine.run_for_match(m, source="on_demand")
            refresh_model_cache(result)
            refreshed.append(m.match_id)
        except Exception as exc:          # isolate, report, move on
            print(f"[refresh-all] {m.match_id} FAILED: {exc}")
            failed.append(m.match_id)
    return {"refreshed": refreshed, "failed": failed,
            "duration_ms": round((time.time() - t0) * 1000),
            "generated_at": utcnow().isoformat()}


# ---------------------------------------------------------------------------
# Watchlist + ripeness timing
# ---------------------------------------------------------------------------
from src.db import TimingAlert, WatchlistItem
from src.timing import compute_timing


class WatchIn(BaseModel):
    match_id: str
    market_id: str
    market_title: str | None = None


@app.get("/api/watchlist")
def get_watchlist():
    """Watched markets, each with its live ripeness score."""
    with SessionLocal() as session:
        items = session.execute(select(WatchlistItem)).scalars().all()
    out = []
    for item in items:
        match = get_match(item.match_id)
        timing = (compute_timing(item.market_id, match.kickoff)
                  if match else {"score": 0, "status": "match_over",
                                 "readings": 0, "components": {}, "reasons": []})
        out.append({
            "match_id": item.match_id,
            "market_id": item.market_id,
            "market_title": item.market_title,
            "watched_since": item.created_at.isoformat(),
            "timing": timing,
        })
    out.sort(key=lambda x: x["timing"]["score"], reverse=True)
    return {"watchlist": out, "alert_threshold": config.RIPENESS_ALERT_THRESHOLD}


@app.post("/api/watchlist")
def add_watch(body: WatchIn):
    if not get_match(body.match_id):
        raise HTTPException(404, f"Unknown match_id '{body.match_id}'")
    with SessionLocal() as session:
        exists = session.execute(
            select(WatchlistItem).where(WatchlistItem.market_id == body.market_id)
        ).scalar_one_or_none()
        if exists:
            return {"status": "already_watching", "market_id": body.market_id}
        session.add(WatchlistItem(match_id=body.match_id, market_id=body.market_id,
                                  market_title=body.market_title))
        session.commit()
    return {"status": "watching", "market_id": body.market_id,
            "note": f"You'll be alerted when the ripeness score crosses "
                    f"{config.RIPENESS_ALERT_THRESHOLD:.0f} with positive edge."}


@app.delete("/api/watchlist/{market_id}")
def remove_watch(market_id: str):
    with SessionLocal() as session:
        item = session.execute(
            select(WatchlistItem).where(WatchlistItem.market_id == market_id)
        ).scalar_one_or_none()
        if not item:
            raise HTTPException(404, "Not on watchlist")
        session.delete(item)
        session.commit()
    return {"status": "removed", "market_id": market_id}


@app.get("/api/timing/{match_id}/{market_id}")
def get_timing(match_id: str, market_id: str):
    """Full ripeness breakdown for any market (watched or not)."""
    match = get_match(match_id)
    if not match:
        raise HTTPException(404, f"Unknown match_id '{match_id}'")
    return compute_timing(market_id, match.kickoff)


@app.get("/api/bots")
def bots_ledger():
    """The strategy-lab: five paper bots, their bankrolls and ledgers.
    Hypothetical money betting real books — a laboratory for which betting
    philosophy actually pays, scored with the same fee model as the
    strategy page."""
    from sqlalchemy import func
    from src.bots import PERSONAS, START_BANKROLL, bankroll
    from src.db import BotPosition, OddsReading
    out = []
    with SessionLocal() as session:
        # newest odds reading per market holding an open position — the 30s
        # poll keeps these fresh near matches; a market with no reading
        # falls back to cost, so equity never invents a price
        open_mids = set(session.execute(
            select(BotPosition.market_id)
            .where(BotPosition.closed_at.is_(None))).scalars())
        marks: dict[str, float] = {}
        if open_mids:
            latest_ids = (select(func.max(OddsReading.id))
                          .where(OddsReading.market_id.in_(open_mids))
                          .group_by(OddsReading.market_id))
            for rd in session.execute(
                    select(OddsReading)
                    .where(OddsReading.id.in_(latest_ids))).scalars():
                if rd.yes_price is not None:
                    marks[rd.market_id] = float(rd.yes_price)
        for bot, persona in PERSONAS.items():
            rows = session.execute(
                select(BotPosition).where(BotPosition.bot == bot)
                .order_by(BotPosition.opened_at.desc())
            ).scalars().all()
            open_pos, closed_pos = [], []
            for r in rows:
                item = {
                    "match_id": r.match_id, "market_id": r.market_id,
                    "market_title": r.market_title,
                    "entry_price": r.entry_price, "contracts": r.contracts,
                    "cost": r.cost, "note": r.note,
                    "opened_at": r.opened_at.isoformat() if r.opened_at else None,
                }
                if r.closed_at is None:
                    mark = marks.get(r.market_id)
                    item["mark_price"] = mark
                    item["market_value"] = (round(r.contracts * mark, 2)
                                            if mark is not None else r.cost)
                    open_pos.append(item)
                else:
                    item.update({
                        "closed_at": r.closed_at.isoformat(),
                        "close_price": r.close_price,
                        "close_reason": r.close_reason,
                        "net": round((r.pnl or 0.0) - r.cost, 2),
                    })
                    closed_pos.append(item)
            wins = sum(1 for c in closed_pos if c["net"] > 0)
            cash = bankroll(bot, session)
            # mark-to-market: open positions at the newest polled price
            # (fee-free mark; realized fees still hit on exit/settlement)
            equity = cash + sum(p["market_value"] for p in open_pos)
            out.append({
                "bot": bot, **persona,
                "bankroll": cash,
                "equity": round(equity, 2),
                "net_pnl": round(equity - START_BANKROLL, 2),
                "open": open_pos,
                "closed": closed_pos[:20],
                "trades": len(closed_pos),
                "wins": wins,
            })
    return {"start_bankroll": START_BANKROLL, "bots": out,
            "generated_at": utcnow().isoformat()}


@app.post("/api/alerts/test")
def alerts_test():
    """Fire a test message through every configured alert channel, so the
    Discord webhook / ntfy topic can be proven before a match, not during."""
    from src.alerts import send_alert
    send_alert("⚡ ACTION channel test — act-now pings (signals, tracker "
               "flips, goals) arrive here.", title="WC26 channel test")
    send_alert("📊 DETAIL channel test — the narrator posts live briefs, "
               "goal analyses and your position table here.", kind="detail")
    return {"sent": True,
            "action_configured": bool(config.DISCORD_ACTION_WEBHOOK_URL),
            "detail_configured": bool(config.DISCORD_DETAIL_WEBHOOK_URL),
            "split": (config.DISCORD_ACTION_WEBHOOK_URL
                      != config.DISCORD_DETAIL_WEBHOOK_URL),
            "ntfy_configured": bool(config.NTFY_TOPIC)}


@app.get("/api/positions")
def positions_list():
    """Son's real tracked positions with live HOLD/EXIT verdicts. In play,
    prices come from the live_auto cycle; pre-match, from the latest
    prediction batch. Read-only: never fires alerts."""
    from src.cache import latest_for_match
    from src.db import MatchLiveSnapshot, TrackedPosition
    from src.live_auto import live_auto
    from src.positions import evaluate_positions
    from src.schedule_data import load_schedule
    out = []
    with SessionLocal() as session:
        match_ids = set(session.execute(
            select(TrackedPosition.match_id)
            .where(TrackedPosition.closed_at.is_(None))).scalars())
        live_ids = set(session.execute(
            select(MatchLiveSnapshot.match_id)).scalars())
    for m in load_schedule():
        if m.match_id not in match_ids:
            continue
        rows = {}
        minute = None
        if m.match_id in live_ids:
            try:
                la = live_auto(m, engine,
                               (latest_for_match(m.match_id) or {}).get("xg"))
                if la.get("available"):
                    rows = {r["market_id"]: r for r in la.get("markets", [])}
                    minute = (la.get("live_state") or {}).get("minutes_elapsed")
            except Exception:
                rows = {}
        if not rows:
            batch = latest_for_match(m.match_id) or {}
            rows = {r["market_id"]: r for r in batch.get("markets", [])}
        out.extend(evaluate_positions(rows, m.match_id, minute))
    return {"positions": out, "generated_at": utcnow().isoformat()}


@app.post("/api/positions")
def positions_add(payload: dict):
    """Record real positions: {"positions": [{match_id, market_id,
    market_title?, entry_price, contracts, cost?, note?}]}. cost defaults
    to contracts*entry_price + the modelled fee."""
    from src.db import TrackedPosition
    added = []
    with SessionLocal() as session:
        for p in payload.get("positions") or []:
            if not p.get("market_id") or not p.get("match_id"):
                continue
            ep = float(p["entry_price"]); n = int(p["contracts"])
            cost = p.get("cost")
            if cost is None:
                cost = round(n * (ep + 0.07 * ep * (1 - ep)), 2)
            pos = TrackedPosition(
                match_id=p["match_id"], market_id=p["market_id"],
                market_title=p.get("market_title") or p["market_id"],
                entry_price=ep, contracts=n, cost=float(cost),
                note=p.get("note") or "")
            session.add(pos)
            session.flush()
            added.append(pos.id)
        session.commit()
    return {"added": len(added), "ids": added}


@app.delete("/api/positions/{pos_id}")
def positions_close(pos_id: int, note: str = Query("closed by user")):
    """Mark a tracked position closed (you exited / it settled)."""
    from src.db import TrackedPosition
    with SessionLocal() as session:
        pos = session.get(TrackedPosition, pos_id)
        if pos is None:
            raise HTTPException(404, f"no tracked position {pos_id}")
        pos.closed_at = utcnow()
        pos.close_note = note
        session.commit()
    return {"closed": pos_id, "note": note}


@app.post("/api/bots/restore")
def bots_restore(payload: dict):
    """Re-insert archived bot positions after a DB wipe — the ledgers are
    the one table the boot self-heal cannot rebuild. Accepts the /api/bots
    response shape (bots[].open/closed) or a bare {"positions": [...]}.
    (bot, market_id) duplicates are skipped, so replay is idempotent and
    it composes with the tick's own deterministic re-entry."""
    from datetime import datetime
    from src.bots import PERSONAS
    from src.db import BotPosition

    def _dt(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(v)
        except (TypeError, ValueError):
            return None

    items = list(payload.get("positions") or [])
    for b in payload.get("bots") or []:
        for p in (b.get("open") or []):
            items.append({**p, "bot": b.get("bot")})
        for p in (b.get("closed") or []):
            items.append({**p, "bot": b.get("bot")})

    inserted = skipped = 0
    with SessionLocal() as session:
        have = {(r.bot, r.market_id)
                for r in session.execute(select(BotPosition)).scalars()}
        for p in items:
            bot, mk = p.get("bot"), p.get("market_id")
            if bot not in PERSONAS or not mk or (bot, mk) in have:
                skipped += 1
                continue
            pnl = p.get("pnl")
            if pnl is None and p.get("net") is not None:
                # /api/bots renders net = pnl - cost; recover the gross
                pnl = round(p["net"] + (p.get("cost") or 0.0), 2)
            kw = dict(bot=bot, match_id=p.get("match_id") or "?",
                      market_id=mk,
                      market_title=p.get("market_title") or "",
                      entry_price=float(p.get("entry_price") or 0.0),
                      contracts=int(p.get("contracts") or 0),
                      cost=float(p.get("cost") or 0.0),
                      note=p.get("note") or "",
                      closed_at=_dt(p.get("closed_at")),
                      close_price=p.get("close_price"),
                      close_reason=p.get("close_reason"), pnl=pnl)
            opened = _dt(p.get("opened_at"))
            if opened is not None:      # omit -> column default (utcnow)
                kw["opened_at"] = opened
            session.add(BotPosition(**kw))
            have.add((bot, mk))
            inserted += 1
        session.commit()
    return {"inserted": inserted, "skipped": skipped}


@app.get("/api/live-signals")
def live_signals(match_id: str | None = Query(None),
                 limit: int = Query(30, ge=1, le=200)):
    """In-play BUY/SELL signals on watched markets, newest first. The live
    box polls this to toast fresh signals and badge watched rows; Discord
    gets the same pushes server-side, so nothing depends on a page being
    open. Optional ?match_id= narrows to one match."""
    from src.db import LiveSignal
    with SessionLocal() as session:
        q = select(LiveSignal).order_by(LiveSignal.created_at.desc()).limit(limit)
        if match_id:
            q = select(LiveSignal).where(LiveSignal.match_id == match_id) \
                .order_by(LiveSignal.created_at.desc()).limit(limit)
        rows = session.execute(q).scalars().all()
    return {"min_diff": config.LIVE_SIGNAL_MIN_DIFF,
            "signals": [
                {
                    "id": r.id,
                    "match_id": r.match_id,
                    "market_id": r.market_id,
                    "market_title": r.market_title,
                    "side": r.side,
                    "kind": r.kind or "watched",
                    "live_probability": r.live_probability,
                    "market_probability": r.market_probability,
                    "difference": r.difference,
                    "minute": r.minute,
                    "fired_at": r.created_at.isoformat(),
                } for r in rows
            ]}


@app.get("/api/alerts/recent")
def recent_alerts(limit: int = Query(20, ge=1, le=100)):
    """Notification feed: every ripeness alert that has fired."""
    with SessionLocal() as session:
        rows = session.execute(
            select(TimingAlert).order_by(TimingAlert.created_at.desc()).limit(limit)
        ).scalars().all()
    return {"alerts": [
        {
            "match_id": r.match_id,
            "market_id": r.market_id,
            "market_title": r.market_title,
            "score": r.score,
            "decimal_odds": r.decimal_odds,
            "edge": r.edge,
            "reasons": r.reasons,
            "fired_at": r.created_at.isoformat(),
        } for r in rows
    ]}


# ---------------------------------------------------------------------------
class SettingsIn(BaseModel):
    min_edge: float | None = None
    min_confidence: float | None = None
    min_volume: float | None = None


@app.get("/api/settings")
def get_settings():
    with SessionLocal() as session:
        return {
            "min_edge": get_setting(session, "min_edge", config.MIN_EDGE),
            "min_confidence": get_setting(session, "min_confidence", config.MIN_CONFIDENCE),
            "min_volume": get_setting(session, "min_volume", config.MIN_VOLUME_24H),
        }


@app.post("/api/settings")
def update_settings(body: SettingsIn):
    with SessionLocal() as session:
        for key, value in body.model_dump(exclude_none=True).items():
            set_setting(session, key, value)
    return {"status": "saved", **get_settings()}
