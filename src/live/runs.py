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
                             PredictionContract, PredictionRun)

GIT_REV = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:40]


def _now():
    return datetime.now(timezone.utc)


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _write_run(s, fixture, run_type: str, model: dict,
               canonical: bool = False) -> PredictionRun | None:
    """The transactional core. Returns the committed run or None."""
    pred = model_mls.predict_fixture(fixture, model, run_type=run_type)
    if pred is None:
        return None
    ko = _utc(fixture.current_kickoff_utc)
    run = PredictionRun(
        fixture_id=fixture.id, run_type=run_type,
        scheduled_for=ko, captured_at=_now(),
        seconds_before_kickoff=int((ko - _now()).total_seconds()),
        status="writing", canonical=False,
        git_revision=GIT_REV,
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
    # contracts: the three-way outcomes, attached to mapped market
    # contracts where they exist
    mapped: dict[str, int] = {}
    me = (s.query(MarketEvent)
          .filter_by(fixture_id=fixture.id, mapping_approved=True)
          .first())
    if me:
        for mc in s.query(MarketContract).filter_by(
                market_event_id=me.id).all():
            if mc.outcome_key:
                mapped[mc.outcome_key] = mc.id
    total = 0.0
    for okey in ("home_win", "draw", "away_win"):
        p = pred["outcomes"][okey]
        total += p
        s.add(PredictionContract(
            prediction_run_id=run.id,
            market_contract_id=mapped.get(okey),
            outcome_key=okey, raw_probability=p))
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
        cutoff = _now() + timedelta(hours=horizon_hours)
        for f in (s.query(Fixture)
                  .filter_by(competition_slug="mls-2026")
                  .filter(Fixture.status.in_(("pre", "in")))
                  .all()):
            if f.current_kickoff_utc is None:
                continue
            ko = _utc(f.current_kickoff_utc)
            if not (_now() - timedelta(hours=3) <= ko <= cutoff):
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
                if _write_run(s, f, "scheduled", model):
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
    """The atomic lock: fixtures kicking off within the lock window get
    their book captured, then ONE canonical complete t10 run."""
    if not (plane_ready() and config.MLS_SHADOW_ENABLED):
        return {"skipped": "off"}
    model = model_mls.current_model()
    if model is None:
        return {"skipped": "no model"}
    s = get_session()
    locked = 0
    try:
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
            # 1. freeze the book first (its own transaction/observation)
            markets.capture_quotes(fixture_id=f.id)
            # 2-12. the transactional run (isolated per fixture: one
            # failed lock must stay visibly missing, not take the rest
            # of the slate down with it)
            try:
                run = _write_run(s, f, "t10", model, canonical=True)
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

        def _payload(run):
            contracts = (s.query(PredictionContract)
                         .filter_by(prediction_run_id=run.id).all())
            # outcome -> Kalshi ticker via the APPROVED mapping chain, so
            # the frontend joins model to book by ticker, never by
            # guessing at side labels
            tickers = {}
            for c in contracts:
                if c.market_contract_id:
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
                             for c in contracts},
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
        return {
            "model_version": model_mls.MODEL_NAME,
            "shadow": True,
            "real_money_signals": config.REAL_MONEY_SIGNALS_ENABLED,
            "latest": _payload(latest),
            "t10_lock": _payload(lock) if lock else None,
        }
    finally:
        s.close()


def shadow_counts() -> dict:
    """Readiness numbers for /api/ready's live section."""
    if not plane_ready():
        return {}
    s = get_session()
    try:
        from src.live.models import Team
        return {
            "teams": s.query(Team).filter_by(
                competition_slug="mls-2026").count(),
            "fixtures": s.query(Fixture).filter_by(
                competition_slug="mls-2026").count(),
            "completed_fixtures": s.query(Fixture).filter_by(
                competition_slug="mls-2026", status="post").count(),
            "complete_runs": s.query(PredictionRun).filter_by(
                status="complete").count(),
            "t10_locks": s.query(PredictionRun).filter_by(
                run_type="t10", canonical=True,
                status="complete").count(),
            "mapped_events": s.query(MarketEvent).filter_by(
                mapping_approved=True).count(),
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
                             for c in contracts},
                "locked": run.run_type == "t10" and run.canonical,
            })
        return out
    finally:
        s.close()
