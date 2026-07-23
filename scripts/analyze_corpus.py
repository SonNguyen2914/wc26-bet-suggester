#!/usr/bin/env python
"""Analyze an exported MLS shadow corpus — WITHOUT a database.

The V8.1 evaluation Phase 3 acceptance test: a researcher downloads the
corpus, runs ONE command, and regenerates the report. This script reads
only the corpus files (it never opens the live database) and:

  1. verifies every file's sha256 and the manifest_hash;
  2. REPLAYS each prediction run from its stored input artifact and
     confirms it reproduces the stored contract probabilities;
  3. scores forecast quality (log loss, Brier) for runs whose fixture
     has a settled result;
  4. compares the model 3-way to the frozen lock book (implied prices);
  5. echoes the audit counts, including missed locks and failed
     snapshots (no survivorship bias).

Usage:
    python scripts/analyze_corpus.py <corpus_dir> [--json]

Model replay uses the committed model code (src.models), which is part
of the repository, not the production database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys

THREE_WAY = ("home_win", "draw", "away_win")


def _load(corpus_dir, name):
    with open(os.path.join(corpus_dir, name), encoding="utf-8") as fh:
        return json.load(fh)


def verify_hashes(corpus_dir, manifest) -> list[str]:
    problems = []
    for name, meta in manifest["files"].items():
        data = _load(corpus_dir, name)
        body = json.dumps(data, sort_keys=True, ensure_ascii=False)
        if hashlib.sha256(body.encode()).hexdigest() != meta["sha256"]:
            problems.append(f"file hash mismatch: {name}")
    core = json.dumps({"files": manifest["files"],
                       "counts": manifest["counts"],
                       "corpus_version": manifest["corpus_version"],
                       "schema_version": manifest["schema_version"]},
                      sort_keys=True)
    if hashlib.sha256(core.encode()).hexdigest() != manifest["manifest_hash"]:
        problems.append("manifest_hash mismatch")
    return problems


def replay_report(corpus_dir) -> dict:
    from src.live.model_mls import replay_from_artifact
    runs = _load(corpus_dir, "prediction_runs.json")
    arts = {a["id"]: a for a in
            _load(corpus_dir, "model_input_artifacts.json")}
    contracts = _load(corpus_dir, "prediction_contracts.json")
    by_run: dict = {}
    for c in contracts:
        if c["outcome_key"] in THREE_WAY:
            by_run.setdefault(c["prediction_run_id"], {})[
                c["outcome_key"]] = c["raw_probability"]
    checked = reproduced = 0
    worst = 0.0
    for r in runs:
        aid = r.get("model_input_artifact_id")
        if aid is None or aid not in arts:
            continue
        doc = json.loads(arts[aid]["document_json"])
        out = replay_from_artifact(doc)
        if out is None:
            continue
        stored = by_run.get(r["id"], {})
        if set(stored) != set(THREE_WAY):
            continue
        delta = max(abs(out[k] - stored[k]) for k in THREE_WAY)
        checked += 1
        worst = max(worst, delta)
        if delta <= 1e-6:
            reproduced += 1
    return {"runs_checked": checked, "reproduced": reproduced,
            "max_delta": worst,
            "all_reproduced": checked > 0 and reproduced == checked}


def forecast_report(corpus_dir) -> dict:
    fixtures = {f["espn_event_id"]: f
                for f in _load(corpus_dir, "fixtures.json")}
    runs = _load(corpus_dir, "prediction_runs.json")
    fix_by_id = {f["id"]: f for f in fixtures.values()}
    contracts = _load(corpus_dir, "prediction_contracts.json")
    by_run: dict = {}
    for c in contracts:
        if c["outcome_key"] in THREE_WAY:
            by_run.setdefault(c["prediction_run_id"], {})[
                c["outcome_key"]] = c["raw_probability"]
    # one scored row per fixture: prefer the canonical t10 lock
    best: dict = {}
    for r in runs:
        f = fix_by_id.get(r["fixture_id"])
        if not f or f.get("status") != "post":
            continue
        if f.get("home_goals") is None or f.get("away_goals") is None:
            continue
        p = by_run.get(r["id"])
        if not p or set(p) != set(THREE_WAY):
            continue
        rank = 2 if (r["run_type"] == "t10" and r["canonical"]) else 1
        cur = best.get(r["fixture_id"])
        if cur is None or rank > cur[0]:
            best[r["fixture_id"]] = (rank, p, f)
    ll = brier = n = hits = 0.0
    for _rank, p, f in best.values():
        hg, ag = f["home_goals"], f["away_goals"]
        result = ("home_win" if hg > ag else
                  "away_win" if ag > hg else "draw")
        q = max(min(p[result], 1 - 1e-9), 1e-9)
        ll += -math.log(q)
        brier += sum((p[k] - (1.0 if k == result else 0.0)) ** 2
                     for k in THREE_WAY)
        hits += 1.0 if max(p, key=p.get) == result else 0.0
        n += 1
    if n == 0:
        return {"scored_fixtures": 0,
                "note": "no settled fixtures with runs yet"}
    return {"scored_fixtures": int(n),
            "log_loss": round(ll / n, 4),
            "brier": round(brier / n, 4),
            "winner_hit_rate": round(hits / n, 4)}


def market_report(corpus_dir) -> dict:
    """Model 3-way vs the frozen lock book (normalized yes-ask implied),
    per canonical lock — the prospective model-vs-executable comparison
    the whole platform exists to build."""
    runs = _load(corpus_dir, "prediction_runs.json")
    contracts = _load(corpus_dir, "prediction_contracts.json")
    quotes = {q["id"]: q for q in _load(corpus_dir,
                                        "market_quotes.json")}
    locks = [r for r in runs if r["run_type"] == "t10"
             and r["canonical"] and r["status"] == "complete"]
    lock_ids = {r["id"] for r in locks}
    rows = 0
    for c in contracts:
        if (c["prediction_run_id"] in lock_ids
                and c["outcome_key"] in THREE_WAY
                and c.get("market_quote_id") in quotes):
            rows += 1
    return {"canonical_locks": len(locks),
            "model_to_frozen_quote_links": rows,
            "note": ("market-vs-model comparison is computable from the "
                     "frozen quotes; full scoring lands with settlement")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    # make src importable when run from the repo root
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))

    manifest = _load(args.corpus_dir, "manifest.json")
    report = {
        "corpus_version": manifest["corpus_version"],
        "schema_version": manifest["schema_version"],
        "manifest_hash": manifest["manifest_hash"],
        "integrity_problems": verify_hashes(args.corpus_dir, manifest),
        "counts": manifest["counts"],
        "reproducibility": replay_report(args.corpus_dir),
        "forecast": forecast_report(args.corpus_dir),
        "market": market_report(args.corpus_dir),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\n  Corpus {report['corpus_version']} "
              f"({report['schema_version']})")
        print(f"  manifest_hash {report['manifest_hash'][:16]}")
        print(f"  integrity: {'CLEAN' if not report['integrity_problems'] else report['integrity_problems']}")
        c = report["counts"]
        print(f"  fixtures {c['fixtures']} · runs {c['prediction_runs']} "
              f"· canonical locks {c['canonical_locks']} · "
              f"missed {c['missed_locks']} · failed snaps "
              f"{c['failed_snapshots']}")
        rp = report["reproducibility"]
        print(f"  reproducibility: {rp['reproduced']}/{rp['runs_checked']} "
              f"runs replay exactly (max delta {rp['max_delta']})")
        print(f"  forecast: {report['forecast']}")
        print(f"  market: {report['market']}\n")
    return 1 if report["integrity_problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
