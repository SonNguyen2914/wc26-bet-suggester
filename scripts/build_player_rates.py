"""Build src/data/player_rates.json from the extracted PMSR dataset.

Per remaining team, per player: matches, starts, attempts, shots on target,
goals (from FIFA's per-player distributions tables — the authoritative goal
credit), and a SMOOTHED share of the team's scoring used by the player-props
model. Every number traces to a FIFA Post-Match Summary Report.

Share model (small samples, be humble):
  raw_goal_share    = player_goals / team_goals
  raw_attempt_share = player_attempts / team_attempts
  share = 0.6 * raw_goal_share + 0.4 * raw_attempt_share
Attempts stabilise the tiny goal samples (5 matches) without inventing
numbers — both inputs are measured. Shares are then normalised so a team's
listed players sum to 1.0 (own goals excluded by construction).
"""
import csv, json, sys
from collections import defaultdict
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1
           else "/Users/ns/Desktop/Projects/WC26 Predictor/match_pdfs/extracted")
OUT = Path(__file__).resolve().parents[1] / "src" / "data" / "player_rates.json"
REMAINING = {"France", "Spain", "England", "Argentina"}   # teams with matches LEFT (finalists + 3P participants)

rows = list(csv.DictReader(open(SRC / "player_match_stats.csv")))
players: dict = defaultdict(lambda: {"matches": 0, "starts": 0, "attempts": 0,
                                     "on_target": 0, "goals": 0, "shirt": None})
team_tot = defaultdict(lambda: {"attempts": 0, "goals": 0})

# on-target per player from the shot table (distributions has no OT column)
ot = defaultdict(int)
for s in csv.DictReader(open(SRC / "shots.csv")):
    if s["team"] in REMAINING and s["on_target"] == "True":
        ot[(s["team"], s["player"].upper())] += 1

for r in rows:
    team = r["team"]
    if team not in REMAINING or not r["attempts"]:
        continue
    key = (team, r["player"].upper())
    p = players[key]
    p["matches"] += 1
    p["starts"] += 1 if r["role"] == "starting" else 0
    p["attempts"] += int(r["attempts"])
    p["goals"] += int(r["goals"])
    p["shirt"] = int(r["shirt"])
    p["name"] = r["player"]
    team_tot[team]["attempts"] += int(r["attempts"])
    team_tot[team]["goals"] += int(r["goals"])

out = {}
for (team, up), p in players.items():
    tt = team_tot[team]
    gs = p["goals"] / tt["goals"] if tt["goals"] else 0.0
    as_ = p["attempts"] / tt["attempts"] if tt["attempts"] else 0.0
    share = 0.6 * gs + 0.4 * as_
    out.setdefault(team, []).append({
        "player": p["name"], "shirt": p["shirt"],
        "matches": p["matches"], "starts": p["starts"],
        "attempts": p["attempts"], "on_target": ot.get((team, up), 0),
        "goals": p["goals"], "share_raw": round(share, 5),
    })

for team, lst in out.items():
    tot = sum(x["share_raw"] for x in lst) or 1.0
    for x in lst:
        x["share"] = round(x["share_raw"] / tot, 5)
        del x["share_raw"]
    lst.sort(key=lambda x: -x["share"])

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    "source": "FIFA Training Centre PMSR distributions tables (43 PDFs through the QFs, validated)",
    "share_model": "0.6*goal_share + 0.4*attempt_share, normalised per team",
    "teams": out}, indent=1))
print(f"wrote {OUT}")
for team in sorted(out):
    top = out[team][:3]
    print(f"  {team:<12} " + " | ".join(
        f"{x['player']} share={x['share']:.2f} ({x['goals']}g/{x['attempts']}att)"
        for x in top))
