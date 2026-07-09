"""FIFA Training Centre Post-Match Summary Report (PMSR) extractor.

Discovers every PMSR PDF from the official match-report hubs, downloads them
to a local archive, and extracts BOTH structured data and lossless raw text
per section — the feed for extending the model to Game Lines, Game Props
(first-goal timing, halves, BTTS...) and Player Props (shot lists per player,
goalscorer timing, lineups).

Usage:
  python scripts/fifa_extract.py --dir "/path/to/match_pdfs"            # all steps
  python scripts/fifa_extract.py --dir ... --skip-download              # re-parse only
  python scripts/fifa_extract.py --dir ... --force-extract              # re-parse all

Outputs, under <dir>:
  PMSR-*.pdf                     the archive (skips files already present)
  extracted/PMSR-*.json          one JSON per match: meta + parsed sections +
                                 raw text of EVERY section (lossless)
  extracted/team_match_stats.csv one row per team per match (key stats)
  extracted/shots.csv            one row per shot attempt (player props feed)
  extracted/index.json           discovery + extraction inventory

Design notes:
  - Section routing is by PAGE TITLE, not page number (pagination varies).
  - Every page's raw text is preserved under raw_sections, so nothing is
    lost even where structured parsing doesn't exist yet.
  - pdftotext -layout (poppler) does the text extraction.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

HUBS = [
    "https://www.fifatrainingcentre.com/en/fifa-world-cup-2026/match-report-hub.php",
    "https://www.fifatrainingcentre.com/en/fifa-world-cup-2026/match-report-hub-knockout-stage.php",
]
BASE = "https://www.fifatrainingcentre.com"
PDF_RE = re.compile(r'href="([^"]*?PMSR-M(\d+)-([A-Z]{3})-V-([A-Z]{3})[^"]*?\.pdf)"', re.I)
UA = {"User-Agent": "wc26-suggester-research/0.1 (personal model calibration)"}


# --------------------------------------------------------------------------
# discovery + download
# --------------------------------------------------------------------------
def discover(session: requests.Session) -> list[dict]:
    """Every PMSR link on the hubs -> [{url, match_no, home_code, away_code}]."""
    seen: dict[str, dict] = {}
    for hub in HUBS:
        try:
            html = session.get(hub, headers=UA, timeout=30).text
        except requests.RequestException as exc:
            print(f"[discover] {hub} failed: {exc}", file=sys.stderr)
            continue
        for m in PDF_RE.finditer(html):
            url = m.group(1)
            if url.startswith("/"):
                url = BASE + url
            name = url.rsplit("/", 1)[-1]
            seen[name] = {"url": url, "file": name,
                          "match_no": int(m.group(2)),
                          "home_code": m.group(3).upper(),
                          "away_code": m.group(4).upper()}
    out = sorted(seen.values(), key=lambda x: x["match_no"])
    print(f"[discover] {len(out)} PMSR PDFs listed on the hubs")
    return out


def download(session: requests.Session, items: list[dict], directory: Path,
             force: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    got = skipped = failed = 0
    for it in items:
        dest = directory / it["file"]
        if dest.exists() and dest.stat().st_size > 100_000 and not force:
            skipped += 1
            continue
        try:
            r = session.get(it["url"], headers=UA, timeout=90)
            r.raise_for_status()
            dest.write_bytes(r.content)
            got += 1
            print(f"[download] {it['file']} ({len(r.content)//1024} KB)")
            time.sleep(0.4)              # be polite to FIFA's CDN
        except requests.RequestException as exc:
            failed += 1
            print(f"[download] {it['file']} FAILED: {exc}", file=sys.stderr)
    print(f"[download] new={got} cached={skipped} failed={failed}")


# --------------------------------------------------------------------------
# text extraction + parsing
# --------------------------------------------------------------------------
def pdf_pages(pdf: Path) -> list[str]:
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                         capture_output=True, text=True, timeout=120).stdout
    return txt.split("\f")


def page_title(page: str) -> str:
    for ln in page.splitlines():
        s = ln.strip()
        # skip the date/venue header line ("07 July 2026 - ... - 12:00")
        if s and not re.match(r"^\d.*2\s?0\s?2\s?6", s):
            return re.sub(r"\s{2,}.*$", "", s)  # drop right-column team name
    return ""


_TWO_COL = re.compile(r"^\s*(\S.*?\S|\S)\s{2,}(\S.*?\S|\S)\s{2,}(\S.*?\S|\S)\s*$")


def parse_two_sided(page: str) -> dict:
    """Generic '<home value>  <label>  <away value>' rows (key stats, phases).
    Values kept as raw strings — honest to the source; numeric coercion is
    the consumer's job."""
    out: dict[str, dict] = {}
    for ln in page.splitlines():
        m = _TWO_COL.match(ln)
        if not m:
            continue
        left, label, right = m.group(1), m.group(2), m.group(3)
        # a stat row has numeric-ish values on both flanks
        if re.match(r"^[\d.,%(]", left) and re.match(r"^[\d.,%(]", right) \
                and re.search(r"[A-Za-z]", label):
            out[label.strip()] = {"home": left.strip(), "away": right.strip()}
    # possession special row: "Total 57.1% 8.5% 34.4% Total"
    pm = re.search(r"Total\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s+Total", page)
    if pm:
        out["Possession"] = {"home": pm.group(1) + "%",
                             "contested": pm.group(2) + "%",
                             "away": pm.group(3) + "%"}
    return out


_SHOT_ROW = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(.+?)\s{2,}"
    r"((?:On Target|Off Target|Deflected|Incomplete|Goal)[^\n]*?)\s{2,}"
    r"([A-Za-z][A-Za-z ]*?)\s{2,}(\S.*?)\s*$")


def parse_shots(page: str, team: str) -> list[dict]:
    """'Attempts at Goal' table rows: time, shirt+player, outcome, body part,
    delivery type. This is the player-props feed (who shot, when, result)."""
    shots = []
    for ln in page.splitlines():
        m = _SHOT_ROW.match(ln)
        if not m:
            continue
        outcome = m.group(4).strip()
        # STRICT goal detection: FIFA's outcome vocabulary includes
        # "On Target - Goal Prevented" (cleared off the line — NOT a goal),
        # which a substring match miscounts. Only the exact outcome counts.
        shots.append({
            "team": team,
            "minute": int(m.group(1)),
            "shirt": int(m.group(2)),
            "player": m.group(3).strip(),
            "outcome": outcome,
            "is_goal": outcome in ("On Target - Goal",
                                   "Deflected On Target - Goal"),
            "on_target": outcome.startswith("On Target"),
            "body_part": m.group(5).strip(),
            "delivery": m.group(6).strip(),
        })
    return shots


def title_team(page: str, home_name: str, away_name: str) -> str | None:
    """Which team an 'Attempts at Goal'/'Set Plays'/... page belongs to —
    the team name sits on the SECTION-TITLE line's right edge. Only that
    line is inspected: the date/venue header line can contain a team name
    by accident ("Mexico City Stadium" ⊃ "Mexico"), which mis-attributed
    England's shots to Mexico until this was title-line-scoped."""
    for ln in page.splitlines()[:6]:
        if "Attempts at Goal" in ln:
            if home_name and home_name in ln:
                return "home"
            if away_name and away_name in ln:
                return "away"
    # fallback: a standalone team-name line just under the title
    for ln in page.splitlines()[:8]:
        st = ln.strip()
        if st == home_name:
            return "home"
        if st == away_name:
            return "away"
    return None


def parse_match(pdf: Path, meta: dict) -> dict:
    pages = pdf_pages(pdf)
    # ---- match meta from page 1: "Argentina 3 - 2 Egypt" etc.
    head = pages[0] if pages else ""
    title_m = re.search(r"^\s*(\S.*?)\s+(\d+)\s*-\s*(\d+)\s+(\S.*?)\s*$",
                        head, re.M)
    home_name = away_name = ""
    score = None
    if title_m:
        home_name, away_name = title_m.group(1).strip(), title_m.group(4).strip()
        score = [int(title_m.group(2)), int(title_m.group(3))]
    stage_m = re.search(r"(Group [A-L]|Round of 32|Round of 16|Quarter-final"
                        r"|Semi-final|Third[- ]place|Final)", head, re.I)
    date_m = re.search(r"(\d{1,2} \w+ 2026)", head)

    out: dict = {
        **meta,
        "home_name": home_name, "away_name": away_name,
        "score": score,
        "stage": stage_m.group(1) if stage_m else None,
        "date": date_m.group(1) if date_m else None,
        "key_stats": {}, "phases": {},
        "shots": [],
        "raw_sections": {},          # lossless: every page's text by title
    }

    for i, page in enumerate(pages):
        t = page_title(page)
        if not t:
            continue
        key = f"p{i+1:02d} {t}"
        out["raw_sections"][key] = page.rstrip()

        if "Key Statistics" in t:
            out["key_stats"] = parse_two_sided(page)
        elif "Phases of Play" in t:
            out["phases"] = parse_two_sided(page)
        elif "Attempts at Goal" in t:
            side = title_team(page, home_name, away_name)
            team = home_name if side == "home" else away_name if side == "away" else "?"
            out["shots"].extend(parse_shots(page, team))

    # derived: first goal + halves split (Game Props feed)
    goals = sorted([s for s in out["shots"] if s["is_goal"]],
                   key=lambda s: s["minute"])
    out["derived"] = {
        "first_goal": goals[0] if goals else None,
        "goals_1h": sum(1 for g in goals if g["minute"] <= 45),
        "goals_2h": sum(1 for g in goals if 45 < g["minute"] <= 90),
        "goals_et": sum(1 for g in goals if g["minute"] > 90),
        "goal_minutes": [g["minute"] for g in goals],
        "scorers": [{"player": g["player"], "team": g["team"],
                     "minute": g["minute"]} for g in goals],
    }
    return out


# --------------------------------------------------------------------------
# combined datasets
# --------------------------------------------------------------------------
def write_csvs(extracted: list[dict], outdir: Path) -> None:
    # team-level key stats: one row per team per match
    stat_labels: list[str] = []
    for e in extracted:
        for k in e["key_stats"]:
            if k not in stat_labels:
                stat_labels.append(k)
    with open(outdir / "team_match_stats.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["match_no", "stage", "date", "team", "opponent",
                    "is_home", "goals_for", "goals_against"] + stat_labels)
        for e in extracted:
            sc = e["score"] or [None, None]
            for side, team, opp, gf, ga in (
                    ("home", e["home_name"], e["away_name"], sc[0], sc[1]),
                    ("away", e["away_name"], e["home_name"], sc[1], sc[0])):
                row = [e["match_no"], e["stage"], e["date"], team, opp,
                       side == "home", gf, ga]
                for lbl in stat_labels:
                    row.append((e["key_stats"].get(lbl) or {}).get(side, ""))
                w.writerow(row)

    # shot-level: one row per attempt
    with open(outdir / "shots.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["match_no", "stage", "team", "minute", "shirt", "player",
                    "outcome", "is_goal", "on_target", "body_part", "delivery"])
        for e in extracted:
            for s in e["shots"]:
                w.writerow([e["match_no"], e["stage"], s["team"], s["minute"],
                            s["shirt"], s["player"], s["outcome"], s["is_goal"],
                            s["on_target"], s["body_part"], s["delivery"]])


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="PDF archive directory")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--force-extract", action="store_true")
    args = ap.parse_args()

    directory = Path(args.dir).expanduser()
    outdir = directory / "extracted"
    outdir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    # ---- authoritative manifest (user-verified): the completeness source --
    manifest_path = Path(__file__).with_name("fifa_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    remaining = set(manifest["remaining_team_codes"])
    per_team: dict[str, int] = {t: 0 for t in remaining}
    items: list[dict] = []
    for x in manifest["matches"]:
        name = x["url"].rsplit("/", 1)[-1].replace("%20", "-")
        codes = re.match(r"PMSR-M(\d+)-([A-Z]{3})-V-([A-Z]{3})", name)
        items.append({"url": x["url"], "file": name,
                      "match_no": x["match_no"],
                      "home_code": codes.group(2) if codes else x["teams"][0],
                      "away_code": codes.group(3) if codes else x["teams"][1]})
        for t in x["teams"]:
            if t in remaining:
                per_team[t] += 1
    print("[manifest] per-team coverage:",
          " ".join(f"{t}={n}" for t, n in sorted(per_team.items())))
    incomplete = [t for t, n in per_team.items()
                  if n != manifest["expected_per_team"]]
    if incomplete:
        print(f"[manifest] INCOMPLETE teams: {incomplete} — refusing to "
              f"proceed (every remaining team must have "
              f"{manifest['expected_per_team']} matches)", file=sys.stderr)
        return 2

    # ---- hub cross-check: flag remaining-team matches the manifest lacks --
    manifest_nos = {x["match_no"] for x in manifest["matches"]}
    hub = discover(session)
    extra = [h for h in hub
             if (h["home_code"] in remaining or h["away_code"] in remaining)
             and h["match_no"] not in manifest_nos]
    if extra:
        print("[cross-check] WARNING — hub lists remaining-team matches "
              "NOT in the manifest (possible missed match!):")
        for h in extra:
            print(f"  M{h['match_no']:02d} {h['home_code']}-{h['away_code']} "
                  f"{h['url']}")
    else:
        print("[cross-check] hub agrees: no remaining-team match beyond the "
              "manifest ✓")

    if not args.skip_download:
        download(session, items, directory, force=args.force_download)

    # extract every PDF that's actually on disk (hub-listed or hand-dropped)
    pdfs = sorted(directory.glob("PMSR-M*.pdf"))
    by_name = {it["file"]: it for it in items}
    extracted: list[dict] = []
    parsed = cached = errors = 0
    for pdf in pdfs:
        dest = outdir / (pdf.stem + ".json")
        meta = by_name.get(pdf.name) or {}
        m = re.match(r"PMSR-M(\d+)-([A-Z]{3})-V-([A-Z]{3})", pdf.stem)  # suffixes (-V2 etc.) fall off naturally
        meta = {"file": pdf.name,
                "match_no": meta.get("match_no") or (int(m.group(1)) if m else None),
                "home_code": meta.get("home_code") or (m.group(2) if m else None),
                "away_code": meta.get("away_code") or (m.group(3) if m else None)}
        if dest.exists() and not args.force_extract:
            extracted.append(json.loads(dest.read_text()))
            cached += 1
            continue
        try:
            data = parse_match(pdf, meta)
            dest.write_text(json.dumps(data, indent=1))
            extracted.append(data)
            parsed += 1
            print(f"[extract] {pdf.name}: stats={len(data['key_stats'])} "
                  f"shots={len(data['shots'])} sections={len(data['raw_sections'])}")
        except Exception as exc:
            errors += 1
            print(f"[extract] {pdf.name} FAILED: {exc}", file=sys.stderr)

    extracted.sort(key=lambda e: e.get("match_no") or 0)
    write_csvs(extracted, outdir)
    (outdir / "index.json").write_text(json.dumps({
        "hub_listed": len(items), "pdfs_on_disk": len(pdfs),
        "parsed_now": parsed, "from_cache": cached, "errors": errors,
        "matches": [{"match_no": e.get("match_no"), "file": e.get("file"),
                     "home": e.get("home_name"), "away": e.get("away_name"),
                     "score": e.get("score"), "stage": e.get("stage"),
                     "shots": len(e.get("shots", [])),
                     "stats": len(e.get("key_stats", {}))}
                    for e in extracted]}, indent=1))
    print(f"[done] {len(extracted)} matches -> {outdir}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
