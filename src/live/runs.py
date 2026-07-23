"""Prediction runs + the atomic T-10 lock (launch decision O7).

Every simulation batch is a PredictionRun with an explicit UUID, status
gating (readers only see status='complete'), a stored deterministic
seed, the model version, and the git revision. The T-10 lock follows
the decision's transactional workflow: capture the book, open the run
as 'writing', simulate, write contracts, validate invariants, mark
complete+canonical, COMMIT — and only then alert (PAPER-labeled). A
crash before commit leaves nothing visible; the partial unique index
makes a second canonical lock physically impossible.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import config
from src.live import markets, model_mls
from src.live.db import get_session, plane_ready
from src.live.models import (Fixture, MarketContract, MarketEvent,
                             ModelVersion, PredictionContract,
                             PredictionRun)

GIT_REV = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:40]


def _now():
    return datetime.now(timezone.utc)


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def approved_model_version(s) -> ModelVersion | None:
    """The enforcement point for F3: run paths may only publish under a
    ModelVersion row that is approved for shadow. Missing or unapproved
    fails CLOSED (no runs), never open."""
    return (s.query(ModelVersion)
            .filter_by(name=model_mls.MODEL_NAME,
                       approved_for_shadow=True)
            .first())


def _write_run(s, fixture, run_type: str, model: dict, mv: ModelVersion,
               canonical: bool = False,
               snapshot: dict | None = None) -> PredictionRun | None:
    """The transactional core. Returns the committed run or None.
    For canonical locks the caller supplies a COMPLETE MarketSnapshot
    (capture_lock_snapshot) — its id and ticker->quote map are frozen
    onto the run and its contracts (V8 evaluation F1/F2)."""
    pred = model_mls.predict_fixture(fixture, model, run_type=run_type)
    if pred is None:
        return None
    ko = _utc(fixture.current_kickoff_utc)
    run = PredictionRun(
        fixture_id=fixture.id, run_type=run_type,
        scheduled_for=ko, captured_at=_now(), created_at=_now(),
        seconds_before_kickoff=int((ko - _now()).total_seconds()),
        status="writing", canonical=False,
        git_revision=GIT_REV,
        model_version_id=mv.id,
        input_snapshot_hash=model_mls.input_hash(fixture, model),
        market_snapshot_id=(snapshot or {}).get("snapshot_id"),
        simulation_seed=pred["seed"],
        simulation_count=config.N_SIMULATIONS,
        payload_json=json.dumps({
            "xg": pred.get("xg"),
            "scorelines": pred.get("scorelines"),
            "props": pred.get("props"),
            "basis": pred.get("basis"),
        })[:100_000])
    s.add(run)
    s.flush()
    # the model's full probability surface: 3-way outcomes, props
    # (totals/BTTS/margins/first-goal/team totals), and scorelines
    prob_for: dict[str, float] = dict(pred["outcomes"])
    prob_for.update(pred.get("props") or {})
    for sl in pred.get("scorelines") or []:
        h, a = sl["score"].split("-")
        prob_for[f"score_{h}_{a}"] = sl["prob"]
    # contracts: the three-way outcomes are ALWAYS stored; every other
    # mapped market the model prices joins the same batch (full-book
    # locks across all families, launch decision O6/O7). Each contract
    # links its frozen snapshot quote where one exists (F2).
    quote_by_ticker = (snapshot or {}).get("quote_by_ticker") or {}
    mapped: dict[str, int] = {}
    ticker_by_mc: dict[int, str] = {}
    extra: list[tuple[int, str]] = []
    seen_keys: set[str] = set()
    for me in (s.query(MarketEvent)
               .filter_by(fixture_id=fixture.id, mapping_approved=True)
               .all()):
        for mc in s.query(MarketContract).filter_by(
                market_event_id=me.id).all():
            if not mc.outcome_key or mc.outcome_key in seen_keys:
                continue
            ticker_by_mc[mc.id] = mc.ticker
            if mc.outcome_key in ("home_win", "draw", "away_win"):
                mapped[mc.outcome_key] = mc.id
                seen_keys.add(mc.outcome_key)
            elif mc.outcome_key in prob_for:
                extra.append((mc.id, mc.outcome_key))
                seen_keys.add(mc.outcome_key)
    total = 0.0
    for okey in ("home_win", "draw", "away_win"):
        p = pred["outcomes"][okey]
        total += p
        mc_id = mapped.get(okey)
        s.add(PredictionContract(
            prediction_run_id=run.id,
            market_contract_id=mc_id,
            market_quote_id=quote_by_ticker.get(
                ticker_by_mc.get(mc_id, "")),
            outcome_key=okey, raw_probability=p))
    for cid, okey in extra:
        s.add(PredictionContract(
            prediction_run_id=run.id, market_contract_id=cid,
            market_quote_id=quote_by_ticker.get(ticker_by_mc.get(cid, "")),
            outcome_key=okey, raw_probability=prob_for[okey]))
    # integrity invariants BEFORE completion (the decision's checklist)
    if not (0.99 <= total <= 1.01):
        run.status = "failed"
        run.failure_reason = f"probabilities sum {total:.4f}"
        s.commit()
        return None
    run.status = "complete"
    run.canonical = canonical
    run.completed_at = _now()
    s.commit()
    return run


def scheduled_runs(horizon_hours: float = 168.0,
                   freshness_hours: float = 4.0) -> dict:
    """Rolling shadow odds: every upcoming fixture inside the horizon
    gets a fresh 'scheduled' run unless one is younger than
    freshness_hours (operator sweeps may pass 0 to force regeneration).
    The default horizon matches the dashboard's seven-day fixture list —
    "odds up and running for all matches" means every visible fixture."""
    if not (plane_ready() and config.MLS_SHADOW_ENABLED):
        return {"skipped": "off"}
    model = model_mls.current_model()
    if model is None:
        return {"skipped": "no model (no completed fixtures ingested)"}
    s = get_session()
    created = skipped = 0
    try:
        mv = approved_model_version(s)
        if mv is None:
            return {"skipped": "model not approved for shadow (F3 gate)"}
        cutoff = _now() + timedelta(hours=horizon_hours)
        # pre-match only (a 'scheduled' run must never masquerade as an
        # in-play read), and never after the canonical lock exists — the
        # lock is the fixture's final pre-match word (F9)
        for f in (s.query(Fixture)
                  .filter_by(competition_slug="mls-2026", status="pre")
                  .all()):
            if f.current_kickoff_utc is None:
                continue
            ko = _utc(f.current_kickoff_utc)
            if not (_now() <= ko <= cutoff):
                continue
            if (s.query(PredictionRun)
                    .filter_by(fixture_id=f.id, run_type="t10",
                               canonical=True, status="complete")
                    .first()):
                skipped += 1
                continue
            fresh = (s.query(PredictionRun)
                     .filter_by(fixture_id=f.id, run_type="scheduled",
                                status="complete")
                     .order_by(PredictionRun.captured_at.desc())
                     .first())
            if fresh and (_now() - _utc(fresh.captured_at)
                          ) < timedelta(hours=freshness_hours):
                skipped += 1
                continue
            # one bad fixture must not kill the whole sweep — the prod
            # boot of Jul 23 lost all 15 runs to a single insert error
            try:
                if _write_run(s, f, "scheduled", model, mv):
                    created += 1
            except Exception as exc:
                s.rollback()
                print(f"[runs] fixture {f.espn_event_id} failed: {exc}")
        return {"created": created, "fresh_skipped": skipped}
    except Exception as exc:
        s.rollback()
        print(f"[runs] scheduled failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def t10_locks() -> dict:
    """The lock sweep: fixtures inside the window get a lock-grade
    market snapshot (validated for completeness) and then ONE canonical
    complete t10 run frozen against it. NO complete snapshot -> NO
    canonical lock (V8 evaluation F1) — the sweep retries every tick
    until kickoff, and a fixture that never captures stays visibly
    missing."""
    if not (plane_ready() and config.MLS_SHADOW_ENABLED):
        return {"skipped": "off"}
    model = model_mls.current_model()
    if model is None:
        return {"skipped": "no model"}
    s = get_session()
    locked = 0
    try:
        mv = approved_model_version(s)
        if mv is None:
            return {"skipped": "model not approved for shadow (F3 gate)"}
        for f in (s.query(Fixture)
                  .filter_by(competition_slug="mls-2026", status="pre")
                  .all()):
            if f.current_kickoff_utc is None:
                continue
            secs = (_utc(f.current_kickoff_utc) - _now()).total_seconds()
            if not (0 < secs <= 11 * 60):
                continue
            if (s.query(PredictionRun)
                    .filter_by(fixture_id=f.id, run_type="t10",
                               canonical=True, status="complete")
                    .first()):
                continue
            # 1. the lock-grade snapshot — completeness-gated; a failed
            # or partial capture records WHY and produces no lock
            snapshot = markets.capture_lock_snapshot(f.id)
            if snapshot is None:
                continue
            # 2-12. the transactional run (isolated per fixture: one
            # failed lock must stay visibly missing, not take the rest
            # of the slate down with it)
            try:
                run = _write_run(s, f, "t10", model, mv, canonical=True,
                                 snapshot=snapshot)
            except Exception as exc:
                s.rollback()
                print(f"[runs] t10 {f.espn_event_id} failed: {exc}")
                continue
            if run:
                locked += 1
                # 13. alert only AFTER commit, PAPER-labeled
                try:
                    from src.alerts import send_alert
                    o = (s.query(PredictionContract)
                         .filter_by(prediction_run_id=run.id).all())
                    probs = {c.outcome_key: c.raw_probability for c in o}
                    send_alert(
                        f"📋 PAPER · MLS T-10 lock — fixture "
                        f"{f.espn_event_id}: "
                        f"H {probs.get('home_win', 0):.0%} / "
                        f"D {probs.get('draw', 0):.0%} / "
                        f"A {probs.get('away_win', 0):.0%} "
                        f"({model_mls.MODEL_NAME}, shadow — not advice)",
                        title="MLS shadow lock")
                except Exception as exc:
                    print(f"[runs] t10 alert failed: {exc}")
        return {"locked": locked}
    except Exception as exc:
        s.rollback()
        print(f"[runs] t10 failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def model_for_event(espn_event_id: str) -> dict | None:
    """The match hub's model section: this fixture's newest complete run
    with full provenance, plus its canonical T-10 lock if one exists.
    None when the fixture is unknown or no complete run exists yet."""
    if not plane_ready():
        return None
    s = get_session()
    try:
        f = (s.query(Fixture)
             .filter_by(competition_slug="mls-2026",
                        espn_event_id=str(espn_event_id)).first())
        if f is None:
            return None

        THREE_WAY = ("home_win", "draw", "away_win")

        def _payload(run):
            contracts = (s.query(PredictionContract)
                         .filter_by(prediction_run_id=run.id).all())
            # outcome -> Kalshi ticker via the APPROVED mapping chain, so
            # the frontend joins model to book by ticker, never by
            # guessing at side labels. Runs now carry contracts for EVERY
            # priced family; "outcomes" stays strictly the 3-way.
            tickers = {}
            for c in contracts:
                if c.market_contract_id and c.outcome_key in THREE_WAY:
                    mc = s.get(MarketContract, c.market_contract_id)
                    if mc:
                        tickers[c.outcome_key] = mc.ticker
            out = {
                "run_id": run.id, "run_type": run.run_type,
                "captured_at": (_utc(run.captured_at).isoformat()
                                if run.captured_at else None),
                "seed": run.simulation_seed,
                "n_simulations": run.simulation_count,
                "outcomes": {c.outcome_key: round(c.raw_probability, 4)
                             for c in contracts
                             if c.outcome_key in THREE_WAY},
                "tickers": tickers,
            }
            if run.payload_json:
                try:
                    out.update(json.loads(run.payload_json))
                except (ValueError, TypeError):
                    pass
            return out

        latest = (s.query(PredictionRun)
                  .filter_by(fixture_id=f.id, status="complete")
                  .order_by(PredictionRun.captured_at.desc())
                  .first())
        if latest is None:
            return None
        lock = (s.query(PredictionRun)
                .filter_by(fixture_id=f.id, run_type="t10",
                           canonical=True, status="complete")
                .first())
        # display policy (V8 evaluation F9): once the canonical lock
        # exists it IS the fixture's model — a later scheduled run must
        # never silently supersede it. "primary" is what the page shows.
        return {
            "model_version": model_mls.MODEL_NAME,
            "shadow": True,
            "real_money_signals": config.REAL_MONEY_SIGNALS_ENABLED,
            "primary": _payload(lock) if lock else _payload(latest),
            "latest": _payload(latest),
            "t10_lock": _payload(lock) if lock else None,
        }
    finally:
        s.close()


def shadow_counts() -> dict:
    """Readiness for /api/ready's live section — counts PLUS the
    operating gates (V8 evaluation F12): shadow_ready is true only when
    the pipeline could actually produce a lock right now, and blockers
    names whatever is in the way."""
    if not plane_ready():
        return {}
    s = get_session()
    try:
        from src.live.models import Team
        teams = s.query(Team).filter_by(
            competition_slug="mls-2026").count()
        mapped = s.query(MarketEvent).filter_by(
            mapping_approved=True).count()
        runs_n = s.query(PredictionRun).filter_by(
            status="complete").count()
        mv = approved_model_version(s)
        # upcoming fixtures (48h) whose teams lack an approved mapped
        # market event — the invariant the decision doc asked for
        horizon = _now() + timedelta(hours=48)
        upcoming = [f for f in s.query(Fixture)
                    .filter_by(competition_slug="mls-2026", status="pre")
                    .all()
                    if f.current_kickoff_utc
                    and _utc(f.current_kickoff_utc) <= horizon]
        unmapped_upcoming = [
            f.espn_event_id for f in upcoming
            if not s.query(MarketEvent).filter_by(
                fixture_id=f.id, mapping_approved=True).first()]
        blockers = []
        if teams < 30:
            blockers.append(f"teams {teams}/30")
        if mv is None:
            blockers.append("model not approved for shadow")
        if runs_n == 0:
            blockers.append("no complete runs")
        if unmapped_upcoming:
            blockers.append(
                f"unmapped upcoming fixtures: {unmapped_upcoming[:5]}")
        return {
            "teams": teams,
            "fixtures": s.query(Fixture).filter_by(
                competition_slug="mls-2026").count(),
            "completed_fixtures": s.query(Fixture).filter_by(
                competition_slug="mls-2026", status="post").count(),
            "complete_runs": runs_n,
            "t10_locks": s.query(PredictionRun).filter_by(
                run_type="t10", canonical=True,
                status="complete").count(),
            "mapped_events": mapped,
            "model_approved_for_shadow": mv is not None,
            "upcoming_48h": len(upcoming),
            "unmapped_upcoming": len(unmapped_upcoming),
            "shadow_ready": not blockers,
            "blockers": blockers,
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def latest_odds() -> list[dict]:
    """Every upcoming fixture's newest complete run — the public shadow
    odds board. Reads ONLY status='complete' runs, per the decision."""
    if not plane_ready():
        return []
    s = get_session()
    out = []
    try:
        for f in (s.query(Fixture)
                  .filter_by(competition_slug="mls-2026")
                  .filter(Fixture.status.in_(("pre", "in")))
                  .all()):
            run = (s.query(PredictionRun)
                   .filter_by(fixture_id=f.id, status="complete")
                   .order_by(PredictionRun.captured_at.desc())
                   .first())
            if run is None:
                continue
            contracts = (s.query(PredictionContract)
                         .filter_by(prediction_run_id=run.id).all())
            out.append({
                "espn_event_id": f.espn_event_id,
                "kickoff": (_utc(f.current_kickoff_utc).isoformat()
                            if f.current_kickoff_utc else None),
                "run_type": run.run_type,
                "captured_at": (_utc(run.captured_at).isoformat()
                                if run.captured_at else None),
                "model_version": model_mls.MODEL_NAME,
                "outcomes": {c.outcome_key: round(c.raw_probability, 4)
                             for c in contracts
                             if c.outcome_key in ("home_win", "draw",
                                                  "away_win")},
                "locked": run.run_type == "t10" and run.canonical,
            })
        return out
    finally:
        s.close()
