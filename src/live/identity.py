"""Identity seeding + resolution for MLS (launch decision O4).

The 30 clubs are seeded from ESPN's teams endpoint (canonical names +
ESPN ids), and the Kalshi name bridges are seeded as APPROVED aliases —
approval here is the operator-reviewed curated map below, not fuzzy
matching. Resolution helpers only ever consult approved rows, per the
decision: fuzzy may propose, the alias table decides.

API-Football ids arrive later via discovery calls (the decision:
discover through the API, don't copy from memory).
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone

import requests

from src.live.db import get_session, plane_ready
from src.live.models import Team, TeamAlias

ESPN_TEAMS = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
              "usa.1/teams")

# Operator-curated Kalshi-name bridges (verified against the live
# KXMLSGAME slates of Jul 22/25). Keys = Kalshi title sides, values =
# the ESPN displayName they attach to. Seeded as APPROVED aliases.
KALSHI_BRIDGES = {
    "Los Angeles G": "LA Galaxy",
    "Los Angeles F": "LAFC",
    "Saint Louis": "St. Louis CITY SC",
    "New York RB": "Red Bull New York",   # ESPN's word order, not "NY Red Bulls"
    "New York City": "New York City FC",
    "Chicago Fire": "Chicago Fire FC",
    "Miami": "Inter Miami CF",
    "Montreal": "CF Montréal",
    "Salt Lake": "Real Salt Lake",
    "Kansas City": "Sporting Kansas City",
    "DC United": "D.C. United",
    "Atlanta": "Atlanta United FC",
    "Austin": "Austin FC",
    "Charlotte": "Charlotte FC",
    "Cincinnati": "FC Cincinnati",
    "Columbus": "Columbus Crew",
    "Colorado": "Colorado Rapids",
    "Dallas": "FC Dallas",
    "Houston": "Houston Dynamo FC",
    "Minnesota": "Minnesota United FC",
    "Nashville": "Nashville SC",
    "New England": "New England Revolution",
    "Orlando": "Orlando City SC",
    "Philadelphia": "Philadelphia Union",
    "Portland": "Portland Timbers",
    "San Diego FC": "San Diego FC",
    "San Jose": "San Jose Earthquakes",
    "Seattle": "Seattle Sounders FC",
    "Toronto": "Toronto FC",
    "Vancouver": "Vancouver Whitecaps",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().replace(".", "").strip()


def fetch_espn_teams() -> list[dict]:
    r = requests.get(ESPN_TEAMS, timeout=15)
    r.raise_for_status()
    leagues = r.json().get("sports", [{}])[0].get("leagues", [{}])[0]
    return [t.get("team", {}) for t in leagues.get("teams", [])]


def seed_teams(espn_teams: list[dict] | None = None) -> dict:
    """Idempotent: insert missing clubs + approved aliases. Returns
    counts. Never raises into the boot chain."""
    if not plane_ready():
        return {"skipped": "dormant"}
    if espn_teams is None:
        espn_teams = fetch_espn_teams()
    added_teams = added_aliases = 0
    s = get_session()
    try:
        existing = {t.canonical_name: t for t in s.query(Team).filter_by(
            competition_slug="mls-2026")}
        for t in espn_teams:
            name = t.get("displayName")
            if not name or name in existing:
                continue
            row = Team(competition_slug="mls-2026", canonical_name=name,
                       abbrev=t.get("abbreviation"),
                       espn_id=str(t.get("id")))
            s.add(row)
            s.flush()
            existing[name] = row
            added_teams += 1
            # ESPN self-aliases (displayName + shortDisplayName), approved
            for alias in {name, t.get("shortDisplayName")}:
                if alias:
                    if not s.query(TeamAlias).filter_by(
                            source="espn", alias=alias).first():
                        s.add(TeamAlias(team_id=row.id, alias=alias,
                                        source="espn", approved=True))
                        added_aliases += 1
        # curated Kalshi bridges -> approved aliases
        by_norm = {norm(t.canonical_name): t for t in existing.values()}
        for kalshi_name, espn_name in KALSHI_BRIDGES.items():
            team = by_norm.get(norm(espn_name))
            if team is None:
                # tolerate ESPN name drift by containment either way
                team = next((t for n, t in by_norm.items()
                             if norm(espn_name) in n or n in norm(espn_name)),
                            None)
            if team is None:
                print(f"[identity] UNMAPPED bridge: {kalshi_name!r} -> "
                      f"{espn_name!r}")
                continue
            if not s.query(TeamAlias).filter_by(
                    source="kalshi", alias=kalshi_name).first():
                s.add(TeamAlias(team_id=team.id, alias=kalshi_name,
                                source="kalshi", approved=True))
                added_aliases += 1
        s.commit()
        total = s.query(Team).filter_by(competition_slug="mls-2026").count()
        return {"teams": total, "added_teams": added_teams,
                "added_aliases": added_aliases,
                "seeded_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        s.rollback()
        print(f"[identity] seed failed: {exc}")
        return {"error": str(exc)[:200]}
    finally:
        s.close()


def resolve(source: str, alias: str) -> Team | None:
    """APPROVED aliases only — the decision's final-attachment rule."""
    s = get_session()
    if s is None:
        return None
    try:
        row = (s.query(TeamAlias)
               .filter_by(source=source, alias=alias, approved=True)
               .first())
        return s.get(Team, row.team_id) if row else None
    finally:
        s.close()


def resolve_espn_name(name: str) -> Team | None:
    """ESPN display names are their own approved aliases."""
    return resolve("espn", name)


def unmapped_upcoming(names: list[str]) -> list[str]:
    """Readiness invariant helper: which of these ESPN names lack an
    approved mapping."""
    return [n for n in names if resolve_espn_name(n) is None]
