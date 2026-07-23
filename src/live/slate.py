"""Slate classification + operational qualification (V8.1 eval step 2).

The evaluation's step-2 acceptance: for every eligible fixture on a
matchday, record exactly ONE state — no fixture disappears because it
failed. This harness produces that scorecard from the evidence the
shadow plane already writes, so when Saturday's real T-10 locks fire
(and every slate after), the slate audits itself. The states:

  PASS               canonical lock, every integrity check green
  EXECUTION_NOT_READY lock exists but the book wasn't tradeable at T-10
                      (valid evidence, flagged — not a failure)
  MISSED             kicked off, shadow-touched, no canonical lock
  CAPTURE_FAILED     a market snapshot failed; no lock
  LOCK_FAILED        a t10 run exists but status='failed'
  SETTLEMENT_FAILED  fixture final, lock present, paper fills unsettled
  LEGACY_UNSCORABLE  completed before the shadow era (no runs)
  PENDING            not yet inside the lock window (expected pre-lock)
  INTEGRITY_FAILED   lock present but an audit check failed

Plus the operational-qualification invariants a mature slate must hold.
This is INTEGRITY classification, not forecast scoring.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.live import audit as live_audit
from src.live.db import get_session, plane_ready
from src.live.models import (Fixture, MarketSnapshot, PaperFill,
                             PaperSignal, PredictionRun)


def _now():
    return datetime.now(timezone.utc)


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _et_date(dt) -> str | None:
    if dt is None:
        return None
    return _utc(dt).astimezone(
        ZoneInfo("America/New_York")).strftime("%Y%m%d")


def _classify(s, f, checks_by_run: dict) -> dict:
    ko = _utc(f.current_kickoff_utc)
    kicked = ko is not None and ko < _now()
    lock = (s.query(PredictionRun)
            .filter_by(fixture_id=f.id, run_type="t10",
                       canonical=True, status="complete").first())
    n_canonical = (s.query(PredictionRun)
                   .filter_by(fixture_id=f.id, run_type="t10",
                              canonical=True, status="complete").count())
    touched = s.query(PredictionRun).filter_by(fixture_id=f.id).count() > 0
    state = "PENDING"
    detail = None
    if lock is not None:
        snap = (s.get(MarketSnapshot, lock.market_snapshot_id)
                if lock.market_snapshot_id else None)
        row = checks_by_run.get(lock.id)
        if row and not row["all_pass"]:
            state = "INTEGRITY_FAILED"
            detail = [k for k, v in row["checks"].items() if not v]
        elif f.status == "post" and _unsettled(s, f.id):
            state = "SETTLEMENT_FAILED"
        elif snap is not None and not snap.execution_ready:
            state = "EXECUTION_NOT_READY"
        else:
            state = "PASS"
    elif kicked:
        if s.query(MarketSnapshot).filter_by(
                fixture_id=f.id, status="failed").first():
            state = "CAPTURE_FAILED"
        elif s.query(PredictionRun).filter_by(
                fixture_id=f.id, run_type="t10", status="failed").first():
            state = "LOCK_FAILED"
        elif touched:
            state = "MISSED"
        else:
            state = "LEGACY_UNSCORABLE"
    return {
        "espn_event_id": f.espn_event_id,
        "kickoff": ko.isoformat() if ko else None,
        "state": state,
        "detail": detail,
        "canonical_locks": n_canonical,
        "lock_run_id": lock.id if lock else None,
    }


def _unsettled(s, fixture_id: int) -> bool:
    return (s.query(PaperFill)
            .join(PaperSignal, PaperFill.paper_signal_id == PaperSignal.id)
            .filter(PaperSignal.fixture_id == fixture_id,
                    PaperFill.status == "open").count() > 0)


def slate_report(date_str: str | None = None) -> dict:
    """Classify every fixture on a matchday (ET date YYYYMMDD; defaults
    to the soonest upcoming slate). Returns per-fixture states, summary
    counts, and the operational-qualification invariants."""
    if not plane_ready():
        return {"skipped": "dormant"}
    if date_str and not re.fullmatch(r"\d{8}", date_str):
        return {"error": "date must be YYYYMMDD"}
    s = get_session()
    try:
        allf = (s.query(Fixture)
                .filter_by(competition_slug="mls-2026").all())
        by_date: dict[str, list] = {}
        for f in allf:
            d = _et_date(f.current_kickoff_utc)
            if d:
                by_date.setdefault(d, []).append(f)
        if date_str is None:
            upcoming = sorted(d for d in by_date
                              if d >= _now().astimezone(
                                  ZoneInfo("America/New_York"))
                              .strftime("%Y%m%d"))
            date_str = upcoming[0] if upcoming else (
                sorted(by_date)[-1] if by_date else None)
        fixtures = by_date.get(date_str, [])
        # one audit pass, indexed by lock run id, so integrity checks
        # ride along without re-querying
        checks_by_run = {}
        for row in live_audit.lock_audit().get("locks", []):
            checks_by_run[row["lock_run_id"]] = row
        rows = [_classify(s, f, checks_by_run) for f in fixtures]
        summary: dict[str, int] = {}
        for r in rows:
            summary[r["state"]] = summary.get(r["state"], 0) + 1
        # operational qualification across the slate
        dup = [r["espn_event_id"] for r in rows if r["canonical_locks"] > 1]
        post_kickoff_locks = []
        for f in fixtures:
            for lk in (s.query(PredictionRun)
                       .filter_by(fixture_id=f.id, run_type="t10",
                                  canonical=True, status="complete").all()):
                if lk.captured_at and f.current_kickoff_utc and \
                        _utc(lk.captured_at) > _utc(f.current_kickoff_utc):
                    post_kickoff_locks.append(f.espn_event_id)
        qualification = {
            "no_duplicate_canonical_locks": not dup,
            "no_post_kickoff_locks": not post_kickoff_locks,
            "every_fixture_classified": all(r["state"] for r in rows),
            "duplicates": dup,
            "post_kickoff_locks": post_kickoff_locks,
        }
        return {
            "slate_date_et": date_str,
            "generated_at": _now().isoformat(),
            "fixtures": len(rows),
            "summary": summary,
            "qualification": qualification,
            "clean_slate": (all(qualification[k] for k in
                                ("no_duplicate_canonical_locks",
                                 "no_post_kickoff_locks",
                                 "every_fixture_classified"))),
            "rows": rows,
        }
    finally:
        s.close()
