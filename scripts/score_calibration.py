"""The complete, deterministic calibration + significance pipeline.

Scores the frozen T-10 locks against settlement truth and runs the full
statistics battery in one place (Jul 21 evaluation, patch 4): descriptive
metrics, AUC, ECE under several binnings, exact binomial tests (one- AND
two-sided), match-cluster bootstraps with a fixed seed, and BOTH trading
replays — the raw-edge rule (descriptive replay) and the live KELLY bot's
anchored-edge rule (they are different rules; conflating them was one of
the evaluation's findings).

Streams per market: RAW model (recovered: (anchored - 0.4*implied)/0.6,
exact because MODEL_WEIGHT was 0.60 for every archived lock), ANCHORED
(what the app quoted), MARKET (implied at T-10 — the executable ASK, so
market-comparison rows are execution comparisons, not neutral forecast
benchmarks; future locks should archive the full book).

Outputs `research_archive/calibration_results.json` (machine-readable,
seeded, versioned); `tests/test_calibration_pipeline.py` pins the numbers
so narrative drift fails CI.

Evidence labels used throughout (see docs/V6/CALIBRATION.md):
  prospective-frozen | reconstructed | descriptive-replay |
  pilot-strategy-result | execution-comparison
"""
from __future__ import annotations

import json
import os
import random
from math import comb, log

ARCH = os.path.join(os.path.dirname(__file__), "..", "research_archive")
FILES = {
    "NOR_ENG": "NOR_ENG.json",
    "ARG_SUI": "ARG_SUI.json",
    "SF1": "SF1.json",
    "SF2": "SF2.json",
    "THIRD": "THIRD_research_full_2026-07-19T0150Z.json",
    "FINAL": "FINAL_research_full_2026-07-19T2213Z.json",
}
W = 0.60                 # MODEL_WEIGHT for every archived lock (constant)
SEED = 26
N_BOOT = 10000

# The 14 knockout advance calls (see docs/V6/CALIBRATION.md): p = home
# advance probability, y = home advanced. First 8 reconstructed, last 6
# prospective-frozen.
ADVANCE_CALLS = [
    ("BRA_NOR", 0.6208, 0, "reconstructed"),
    ("MEX_ENG", 0.4789, 0, "reconstructed"),
    ("POR_ESP", 0.2972, 0, "reconstructed"),
    ("USA_BEL", 0.3987, 0, "reconstructed"),
    ("ARG_EGY", 0.6864, 1, "reconstructed"),
    ("SUI_COL", 0.5082, 1, "reconstructed"),
    ("MAR_FRA", 0.3850, 0, "reconstructed"),
    ("ESP_BEL", 0.5678, 1, "reconstructed"),
    ("NOR_ENG", 0.4470, 0, "prospective-frozen"),
    ("ARG_SUI", 0.5450, 1, "prospective-frozen"),
    ("SF1", 0.5260, 0, "prospective-frozen"),
    ("SF2", 0.4800, 0, "prospective-frozen"),
    ("THIRD", 0.5290, 0, "prospective-frozen"),
    ("FINAL", 0.5470, 1, "prospective-frozen"),
]


def load_rows() -> list[dict]:
    truth = json.load(open(os.path.join(
        ARCH, "settlements_backfill_2026-07-21.json")))
    rows = []
    for mid, fname in FILES.items():
        d = json.load(open(os.path.join(ARCH, fname)))
        res = truth[mid]["results"]
        for r in d.get("final_lock") or []:
            mk = r["market_id"]
            if mk not in res:
                continue
            anch = r["model_probability"]
            imp = r["implied_probability"]
            raw = max(0.0, min(1.0, (anch - (1 - W) * imp) / W))
            rows.append(dict(match=mid, mk=mk, fam=mk.split("-")[0],
                             raw=raw, anch=anch, imp=imp,
                             y=1.0 if res[mk] == "yes" else 0.0,
                             title=r.get("market_title", "")))
    return rows


# --- metric primitives -----------------------------------------------------

def brier(sel, key):
    return sum((r[key] - r["y"]) ** 2 for r in sel) / len(sel)


def logloss(sel, key, eps=1e-4):
    tot = 0.0
    for r in sel:
        p = min(1 - eps, max(eps, r[key]))
        tot += -(r["y"] * log(p) + (1 - r["y"]) * log(1 - p))
    return tot / len(sel)


def auc(sel, key):
    pos = [r[key] for r in sel if r["y"] == 1]
    neg = [r[key] for r in sel if r["y"] == 0]
    wins = sum(1.0 for p in pos for q in neg if p > q) \
        + 0.5 * sum(1.0 for p in pos for q in neg if p == q)
    return wins / (len(pos) * len(neg))


def ece_width(sel, key, bins):
    """Equal-width ECE, project-style boundaries lo/bins <= p < (lo+1)/bins."""
    n = len(sel)
    tot = 0.0
    for lo in range(bins):
        b = [r for r in sel if lo / bins <= r[key] < (lo + 1) / bins]
        if not b:
            continue
        mp = sum(r[key] for r in b) / len(b)
        rf = sum(r["y"] for r in b) / len(b)
        tot += len(b) / n * abs(mp - rf)
    return tot


def ece_count(sel, key, bins):
    """Equal-count ECE (quantile bins, stable sort)."""
    n = len(sel)
    order = sorted(sel, key=lambda r: r[key])
    size, rem = divmod(n, bins)
    tot, i = 0.0, 0
    for b_i in range(bins):
        take = size + (1 if b_i < rem else 0)
        b = order[i:i + take]
        i += take
        if not b:
            continue
        mp = sum(r[key] for r in b) / len(b)
        rf = sum(r["y"] for r in b) / len(b)
        tot += len(b) / n * abs(mp - rf)
    return tot


def binomial_p(hits, n):
    """Exact binomial vs 0.5: (one_sided, two_sided). Two-sided doubles
    the smaller tail (symmetric null), capped at 1."""
    one = sum(comb(n, k) for k in range(hits, n + 1)) / 2 ** n
    lower = sum(comb(n, k) for k in range(0, n - hits + 1)) / 2 ** n
    return one, min(1.0, 2 * min(one, lower))


# --- bootstraps (deterministic, seed committed) ----------------------------

def cluster_bootstrap(rows, seed=SEED, n_boot=N_BOOT):
    """Resample MATCHES with replacement; returns per-draw lists of
    raw-vs-market Brier skill, anchored-vs-market skill, and
    (market ECE10 - raw ECE10)."""
    matches = sorted({r["match"] for r in rows})
    by_m = {m: [r for r in rows if r["match"] == m] for m in matches}
    rng = random.Random(seed)
    sk_raw, sk_anch, ece_diff = [], [], []
    for _ in range(n_boot):
        sample = [r for m in (rng.choice(matches) for _ in matches)
                  for r in by_m[m]]
        bm = brier(sample, "imp")
        if bm > 0:
            sk_raw.append(1 - brier(sample, "raw") / bm)
            sk_anch.append(1 - brier(sample, "anch") / bm)
        ece_diff.append(ece_width(sample, "imp", 10)
                        - ece_width(sample, "raw", 10))
    return sk_raw, sk_anch, ece_diff


def advance_bootstrap(seed=SEED, n_boot=N_BOOT):
    """Bootstrap the 14 advance Briers against the 0.25 coin-flip."""
    briers = [(p - y) ** 2 for _, p, y, _ in ADVANCE_CALLS]
    rng = random.Random(seed)
    out = []
    for _ in range(n_boot):
        s = [rng.choice(briers) for _ in briers]
        out.append(0.25 - sum(s) / len(s))
    return out


def ci95(xs):
    s = sorted(xs)
    return s[int(0.025 * len(s))], s[int(0.975 * len(s))]


# --- trading replays -------------------------------------------------------

def replay(rows, key, min_edge=0.05, lo=0.10, hi=0.90):
    """Flat $1 on every lock whose `key`-stream edge over implied clears
    min_edge inside the price band, at implied + entry fee. key='raw' is
    the DESCRIPTIVE REPLAY (a retrospectively specified rule); key='anch'
    is the live KELLY bot's gate (its 5pt anchored edge ~ 8.33pt raw)."""
    bets = [r for r in rows
            if (r[key] - r["imp"]) >= min_edge and lo <= r["imp"] <= hi]
    pnl, wins = 0.0, 0
    for r in bets:
        fee = 0.07 * r["imp"] * (1 - r["imp"])
        contracts = 1.0 / (r["imp"] + fee)
        if r["y"]:
            pnl += contracts - 1.0
            wins += 1
        else:
            pnl -= 1.0
    return {"n": len(bets), "wins": wins, "pnl": round(pnl, 4),
            "roi": round(pnl / len(bets), 4) if bets else None}


# --- the pipeline ----------------------------------------------------------

def compute() -> dict:
    rows = load_rows()
    streams = ("raw", "anch", "imp")
    desc = {k: {"brier": brier(rows, k), "logloss": logloss(rows, k),
                "auc": auc(rows, k), "ece10_width": ece_width(rows, k, 10)}
            for k in streams}
    sensitivity = {f"width{b}": {k: ece_width(rows, k, b) for k in streams}
                   for b in (5, 10, 15)}
    sensitivity.update({f"count{b}": {k: ece_count(rows, k, b)
                                      for k in streams} for b in (5, 10)})
    sk_raw, sk_anch, ece_diff = cluster_bootstrap(rows)
    adv = advance_bootstrap()
    hits = sum(1 for _, p, y, _ in ADVANCE_CALLS
               if (p >= 0.5) == (y == 1))
    one, two = binomial_p(hits, len(ADVANCE_CALLS))
    adv_brier = sum((p - y) ** 2 for _, p, y, _ in ADVANCE_CALLS) \
        / len(ADVANCE_CALLS)
    return {
        "metadata": {
            "generated_by": "scripts/score_calibration.py",
            "seed": SEED, "n_boot": N_BOOT, "model_weight": W,
            "n_rows": len(rows), "n_matches": len(FILES),
            "market_stream_semantics": "executable ask at T-10 "
                                       "(execution comparison, not a "
                                       "neutral forecast benchmark)",
            "binomial_sidedness": "both reported",
        },
        "descriptive": desc,
        "ece_sensitivity": sensitivity,
        "cluster_bootstrap": {
            "raw_skill_vs_market": {"ci95": ci95(sk_raw),
                                    "p_gt0": sum(1 for s in sk_raw if s > 0)
                                    / len(sk_raw)},
            "anch_skill_vs_market": {"ci95": ci95(sk_anch),
                                     "p_gt0": sum(1 for s in sk_anch
                                                  if s > 0) / len(sk_anch)},
            "market_minus_raw_ece10": {"ci95": ci95(ece_diff),
                                       "p_gt0": sum(1 for s in ece_diff
                                                    if s > 0)
                                       / len(ece_diff)},
        },
        "advance_calls": {
            "n": len(ADVANCE_CALLS), "hits": hits,
            "binomial_one_sided": one, "binomial_two_sided": two,
            "brier": adv_brier,
            "skill_vs_coin_ci95": ci95(adv),
            "labels": {mid: lab for mid, _, _, lab in ADVANCE_CALLS},
        },
        "replays": {
            "raw_edge_5pt_descriptive": replay(rows, "raw"),
            "live_kelly_rule_anchored_5pt": replay(rows, "anch"),
        },
    }


def main() -> dict:
    res = compute()
    d = res["descriptive"]
    print(f"N = {res['metadata']['n_rows']} markets, "
          f"{res['metadata']['n_matches']} matches "
          f"(market stream = executable ask)")
    print(f"{'stream':9} {'Brier':>8} {'LogLoss':>8} {'AUC':>7} {'ECE10':>7}")
    for k, name in (("raw", "RAW"), ("anch", "ANCHORED"), ("imp", "MARKET")):
        s = d[k]
        print(f"{name:9} {s['brier']:8.4f} {s['logloss']:8.4f} "
              f"{s['auc']:7.3f} {s['ece10_width']:7.4f}")
    print("\nECE sensitivity (spec: raw | anch | market — winner shifts):")
    for spec, v in res["ece_sensitivity"].items():
        best = min(v, key=lambda k: v[k])
        print(f"  {spec:8} {v['raw']:.4f} | {v['anch']:.4f} | "
              f"{v['imp']:.4f}   best: {best}")
    cb = res["cluster_bootstrap"]
    print("\ncluster bootstrap (matches as clusters, seed "
          f"{res['metadata']['seed']}):")
    for k, v in cb.items():
        lo, hi = v["ci95"]
        print(f"  {k:24} CI95 [{lo:+.4f}, {hi:+.4f}]  P(>0)={v['p_gt0']:.2f}")
    a = res["advance_calls"]
    print(f"\nadvance calls: {a['hits']}/{a['n']}  "
          f"one-sided p={a['binomial_one_sided']:.4f}  "
          f"TWO-sided p={a['binomial_two_sided']:.4f}  "
          f"Brier {a['brier']:.4f} "
          f"skill CI {tuple(round(x, 4) for x in a['skill_vs_coin_ci95'])}")
    for name, r in res["replays"].items():
        print(f"replay {name}: n={r['n']} wins={r['wins']} "
              f"pnl={r['pnl']:+.2f} roi="
              f"{'-' if r['roi'] is None else f'{r['roi']:+.1%}'}")
    out = os.path.join(ARCH, "calibration_results.json")
    json.dump(res, open(out, "w"), indent=1)
    print(f"\nwrote {os.path.relpath(out)}")
    return res


if __name__ == "__main__":
    main()
