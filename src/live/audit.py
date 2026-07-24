"""Lock acceptance audit (V8.1 evaluation): a machine-readable check
of every T-10 lock's integrity invariants, plus RETAINED evidence of
what went wrong — missed locks and failed snapshots are data, not
noise. This is INTEGRITY auditing (does the observation hold up?), not
forecast scoring (is the model any good?) — the latter is a separate
prospective study that needs settled results.

Scope = fixtures the shadow pipeline has actually touched (any
prediction run), so historical pre-shadow fixtures never count as
"missed". The output carries a content hash so an exported copy can be
proven to match the database it came from.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from src.live.db import get_session, plane_ready
from src.live.models import (Fixture, MarketSnapshot, ModelInputArtifact,
                             PredictionContract, PredictionRun)

AUDIT_VERSION = "mls-lock-audit-v1"
THREE_WAY = ("home_win", "draw", "away_win")


def _now():
    return datetime.now(timezone.utc)


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _lock_checks(s, f, lock, n_locks) -> dict:
    """Every invariant from the evaluation's acceptance table, as a flat
    dict of booleans. all_pass is their AND."""
    ko = _utc(f.current_kickoff_utc)
    contracts = (s.query(PredictionContract)
                 .filter_by(prediction_run_id=lock.id).all())
    okeys = [c.outcome_key for c in contracts]
    three = {c.outcome_key: c.raw_probability
             for c in contracts if c.outcome_key in THREE_WAY}
    # a market-priced contract is one attached to a market contract; it
    # must carry the frozen quote link
    priced = [c for c in contracts if c.market_contract_id is not None]
    snap = (s.get(MarketSnapshot, lock.market_snapshot_id)
            if lock.market_snapshot_id else None)
    # a later COMPLETE run must not exist for this fixture (F9)
    later = (s.query(PredictionRun)
             .filter_by(fixture_id=f.id, status="complete")
             .filter(PredictionRun.captured_at > lock.captured_at)
             .filter(PredictionRun.id != lock.id).count())
    checks = {
        "exactly_one_canonical_lock": n_locks == 1,
        "lock_before_kickoff": bool(
            ko and lock.captured_at and _utc(lock.captured_at) < ko),
        "lock_inside_window": (lock.seconds_before_kickoff is not None
                               and 0 < lock.seconds_before_kickoff
                               <= 11 * 60),
        "snapshot_present": snap is not None,
        "snapshot_complete": bool(snap and snap.status == "complete"),
        "snapshot_policy_version": bool(snap and snap.policy_version),
        "required_families_complete": bool(
            snap and snap.required_families_complete),
        "contracts_unique_by_outcome": len(okeys) == len(set(okeys)),
        "priced_contracts_quote_linked": all(
            c.market_quote_id is not None for c in priced),
        "model_version_present": lock.model_version_id is not None,
        "model_approved_at_run": bool(lock.model_approved_at_run),
        "input_hash_present": bool(lock.input_snapshot_hash),
        "input_artifact_retained": lock.model_input_artifact_id is not None,
        # a lineup snapshot must be REFERENCED (Phase 5) — whether or not
        # the lineup was confirmed. Its absence is a provenance gap; a
        # PENDING lineup inside it is honest data, not a failure.
        "lineup_snapshot_referenced": lock.lineup_snapshot_id is not None,
        "input_quality_recorded": bool(lock.input_quality_json),
        "seed_present": lock.simulation_seed is not None,
        "three_way_present": set(three) == set(THREE_WAY),
        "three_way_sums_to_one": abs(sum(three.values()) - 1.0) < 0.02
        if len(three) == 3 else False,
        "no_post_kickoff_replacement": later == 0,
    }
    row = {
        "espn_event_id": f.espn_event_id,
        "kickoff": ko.isoformat() if ko else None,
        "lock_run_id": lock.id,
        "captured_at": (_utc(lock.captured_at).isoformat()
                        if lock.captured_at else None),
        "seconds_before_kickoff": lock.seconds_before_kickoff,
        "market_snapshot_id": lock.market_snapshot_id,
        "contracts": len(contracts),
        "priced_contracts": len(priced),
        "input_quality": (json.loads(lock.input_quality_json)
                          if lock.input_quality_json else None),
        "checks": checks,
        "all_pass": all(checks.values()),
    }
    if snap is not None:
        row["snapshot"] = {
            "status": snap.status,
            "policy_version": snap.policy_version,
            "provider_schema_version": snap.provider_schema_version,
            "events_expected": snap.events_expected,
            "events_captured": snap.events_captured,
            "contracts_expected": snap.contracts_expected,
            "quotes_written": snap.quotes_written,
            "quotes_with_prices": snap.quotes_with_prices,
            "quotes_without_prices": snap.quotes_without_prices,
            "depth_rows": snap.depth_rows_written,
            "oldest_quote_age_seconds": snap.oldest_quote_age_seconds,
            "execution_ready": snap.execution_ready,
        }
    return row


def verify_replay(run_id: str, tol: float = 1e-6) -> dict:
    """Independent-reproducibility check (Phase 2 acceptance): replay a
    run FROM ITS STORED INPUT ARTIFACT ALONE and confirm the outcomes
    match the stored contracts. This is the proof behind the
    'independently model-reproducible' claim, so it reads only the
    artifact bytes — never the live ratings — to reconstruct."""
    import json as _json

    from src.live import model_mls
    if not plane_ready():
        return {"skipped": "dormant"}
    s = get_session()
    try:
        run = s.get(PredictionRun, run_id)
        if run is None or run.model_input_artifact_id is None:
            return {"run_id": run_id, "replayable": False,
                    "reason": "no input artifact"}
        art = s.get(ModelInputArtifact, run.model_input_artifact_id)
        doc = _json.loads(art.document_json)
        # engine-drift guard (V9 eval F4): if the artifact froze an engine
        # signature (v2+), it must match the current engine or we REFUSE
        # to replay — a mismatch means the constants/runtime moved and the
        # numbers would silently diverge. "Replayable under the matching
        # engine", never "bit-identical from the bytes alone across
        # versions" (the narrowed, honest claim).
        frozen_sig = (doc.get("engine") or {}).get("signature_hash")
        if frozen_sig is not None:
            current_sig = model_mls.engine_signature()["signature_hash"]
            if frozen_sig != current_sig:
                return {"run_id": run_id, "replayable": False,
                        "reason": "engine signature mismatch — refusing to "
                                  "replay under a different engine",
                        "frozen_engine": frozen_sig[:16],
                        "current_engine": current_sig[:16],
                        "artifact_hash": art.content_hash,
                        "schema_version": art.schema_version}
        replayed = model_mls.replay_from_artifact(doc)
        if replayed is None:
            return {"run_id": run_id, "replayable": False,
                    "reason": "artifact missing ratings"}
        stored = {c.outcome_key: c.raw_probability
                  for c in s.query(PredictionContract)
                  .filter_by(prediction_run_id=run_id).all()
                  if c.outcome_key in THREE_WAY}
        deltas = {k: abs(replayed.get(k, 0.0) - stored.get(k, 0.0))
                  for k in THREE_WAY}
        return {
            "run_id": run_id,
            "replayable": all(d <= tol for d in deltas.values()),
            "max_delta": max(deltas.values()) if deltas else None,
            "artifact_hash": art.content_hash,
            "schema_version": art.schema_version,
        }
    finally:
        s.close()


def lock_audit() -> dict:
    """Integrity audit over every shadow-touched fixture."""
    if not plane_ready():
        return {"skipped": "dormant"}
    s = get_session()
    try:
        touched_ids = {r[0] for r in s.query(
            PredictionRun.fixture_id).distinct().all()}
        fixtures = [f for f in s.query(Fixture).filter_by(
            competition_slug="mls-2026").all() if f.id in touched_ids]
        locks_out, missed = [], []
        for f in fixtures:
            ko = _utc(f.current_kickoff_utc)
            kicked_off = ko is not None and ko < _now()
            canon = (s.query(PredictionRun)
                     .filter_by(fixture_id=f.id, run_type="t10",
                                canonical=True, status="complete")
                     .order_by(PredictionRun.captured_at.desc()).all())
            if not canon:
                # a fixture that has kicked off with no lock is retained
                # evidence, not silence (the evaluation's instruction)
                if kicked_off:
                    fails = (s.query(MarketSnapshot)
                             .filter_by(fixture_id=f.id, status="failed")
                             .count())
                    missed.append({
                        "espn_event_id": f.espn_event_id,
                        "kickoff": ko.isoformat() if ko else None,
                        "failed_snapshot_attempts": fails,
                    })
                continue
            for lock in canon:
                locks_out.append(_lock_checks(s, f, lock, len(canon)))
        failed_snaps = [{
            "market_snapshot_id": sn.id,
            "fixture_id": sn.fixture_id,
            "captured_at": (_utc(sn.captured_at).isoformat()
                            if sn.captured_at else None),
            "failure_reason": sn.failure_reason,
        } for sn in s.query(MarketSnapshot).filter_by(
            status="failed").all()]
        body = {
            "audit_version": AUDIT_VERSION,
            "generated_at": _now().isoformat(),
            "locks": locks_out,
            "missed_locks": missed,
            "failed_snapshots": failed_snaps,
            "summary": {
                "shadow_touched_fixtures": len(fixtures),
                "canonical_locks": len(locks_out),
                "locks_all_pass": sum(1 for r in locks_out
                                      if r["all_pass"]),
                "locks_with_failures": sum(1 for r in locks_out
                                           if not r["all_pass"]),
                "missed_locks": len(missed),
                "failed_snapshots": len(failed_snaps),
                "clean": (all(r["all_pass"] for r in locks_out)
                          and not missed),
            },
        }
        # content hash over canonical serialization EXCLUDING the
        # wall-clock generated_at, so the same DB state hashes identically
        core = {k: v for k, v in body.items() if k != "generated_at"}
        body["content_hash"] = hashlib.sha256(
            json.dumps(core, sort_keys=True).encode()).hexdigest()
        return body
    finally:
        s.close()
