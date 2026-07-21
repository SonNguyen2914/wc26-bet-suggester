"""Score the frozen T-10 locks against settlement truth.
Streams per market: RAW model (recovered: (anchored - 0.4*implied)/0.6,
exact because MODEL_WEIGHT was 0.60 for every archived lock),
ANCHORED (what the app quoted), MARKET (implied at T-10).
Full write-up: docs/V6/CALIBRATION.md
"""
import json
import os
from math import log

ARCH = os.path.join(os.path.dirname(__file__), "..", "research_archive")
FILES = {
    "NOR_ENG": f"{ARCH}/NOR_ENG.json",
    "ARG_SUI": f"{ARCH}/ARG_SUI.json",
    "SF1": f"{ARCH}/SF1.json",
    "SF2": f"{ARCH}/SF2.json",
    "THIRD": f"{ARCH}/THIRD_research_full_2026-07-19T0150Z.json",
    "FINAL": f"{ARCH}/FINAL_research_full_2026-07-19T2213Z.json",
}
truth = json.load(open(f"{ARCH}/settlements_backfill_2026-07-21.json"))
W = 0.60

rows = []
for mid, path in FILES.items():
    d = json.load(open(path))
    res = truth[mid]["results"]
    for r in d.get("final_lock") or []:
        mk = r["market_id"]
        if mk not in res:
            continue
        anch = r["model_probability"]
        imp = r["implied_probability"]
        raw = max(0.0, min(1.0, (anch - (1 - W) * imp) / W))
        y = 1.0 if res[mk] == "yes" else 0.0
        fam = mk.split("-")[0]
        rows.append(dict(match=mid, mk=mk, fam=fam, raw=raw, anch=anch,
                         imp=imp, y=y, title=r.get("market_title", "")))

def brier(sel, key):
    xs = [(r[key] - r["y"]) ** 2 for r in sel]
    return sum(xs) / len(xs)

def logloss(sel, key):
    eps = 1e-4
    tot = 0.0
    for r in sel:
        p = min(1 - eps, max(eps, r[key]))
        tot += -(r["y"] * log(p) + (1 - r["y"]) * log(1 - p))
    return tot / len(sel)

print(f"N = {len(rows)} markets across {len(FILES)} matches")
print(f"base rate (yes) = {sum(r['y'] for r in rows)/len(rows):.3f}")
print()
print(f"{'stream':10} {'Brier':>8} {'LogLoss':>8}")
for key, name in (("raw", "RAW model"), ("anch", "ANCHORED"),
                  ("imp", "MARKET")):
    print(f"{name:10} {brier(rows,key):8.4f} {logloss(rows,key):8.4f}")
ref = brier(rows, "imp")
print(f"\nskill vs market (1 - BS/BS_mkt): raw "
      f"{1 - brier(rows,'raw')/ref:+.3f}  anchored {1 - brier(rows,'anch')/ref:+.3f}")

print("\n--- per match (N, Brier raw | anch | mkt) ---")
for mid in FILES:
    sel = [r for r in rows if r["match"] == mid]
    print(f"{mid:8} {len(sel):3}  {brier(sel,'raw'):.4f} | "
          f"{brier(sel,'anch'):.4f} | {brier(sel,'imp'):.4f}")

print("\n--- per family (N, Brier raw | mkt) ---")
fams = sorted({r["fam"] for r in rows})
for f in fams:
    sel = [r for r in rows if r["fam"] == f]
    tag = "model" if brier(sel, "raw") < brier(sel, "imp") else "market"
    print(f"{f:15} {len(sel):3}  {brier(sel,'raw'):.4f} | "
          f"{brier(sel,'imp'):.4f}   <- {tag}")

print("\n--- calibration, RAW model (bucket: n, mean p, realized) ---")
for lo in range(0, 100, 10):
    sel = [r for r in rows if lo/100 <= r["raw"] < lo/100 + 0.1]
    if not sel:
        continue
    mp = sum(r["raw"] for r in sel)/len(sel)
    rf = sum(r["y"] for r in sel)/len(sel)
    print(f"{lo:3}-{lo+10:3}%  n={len(sel):3}  pred {mp:.3f}  real {rf:.3f}")

print("\n--- calibration, MARKET (bucket: n, mean p, realized) ---")
for lo in range(0, 100, 10):
    sel = [r for r in rows if lo/100 <= r["imp"] < lo/100 + 0.1]
    if not sel:
        continue
    mp = sum(r["imp"] for r in sel)/len(sel)
    rf = sum(r["y"] for r in sel)/len(sel)
    print(f"{lo:3}-{lo+10:3}%  n={len(sel):3}  pred {mp:.3f}  real {rf:.3f}")

# the KELLY-rule subset: raw edge >= 5pts, price in [0.10, 0.90]
print("\n--- flat $1 on every >=5pt raw-edge lock, at implied + fee ---")
bets = [r for r in rows if (r["raw"] - r["imp"]) >= 0.05
        and 0.10 <= r["imp"] <= 0.90]
pnl = 0.0
wins = 0
for r in bets:
    fee = 0.07 * r["imp"] * (1 - r["imp"])
    cost = r["imp"] + fee
    contracts = 1.0 / cost
    if r["y"]:
        pnl += contracts * 1.0 - 1.0
        wins += 1
    else:
        pnl -= 1.0
print(f"n={len(bets)}  wins={wins}  flat-$1 P&L = {pnl:+.2f} "
      f"(ROI {pnl/len(bets)*100:+.1f}%)" if bets else "no qualifying bets")

# headline: the match-winner call per match (advance key, else win90 fav)
print("\n--- the headline calls ---")
for mid in FILES:
    sel = [r for r in rows if r["match"] == mid
           and r["mk"].split("-")[0] in ("KXWCADVANCE", "KXMENWORLDCUP")]
    if not sel:
        sel = [r for r in rows if r["match"] == mid
               and r["mk"].split("-")[0] in ("KXWCGAME", "KXWCMOV")]
    if not sel:
        continue
    pick = max(sel, key=lambda r: r["raw"])
    hit = "HIT " if pick["y"] else "MISS"
    print(f"{mid:8} {hit} raw {pick['raw']:.3f} mkt {pick['imp']:.3f}  "
          f"{pick['title'][:44]}")

json.dump(rows, open(f"{ARCH}/calibration_scored_rows.json", "w"))
