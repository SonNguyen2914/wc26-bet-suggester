"""World Cup 2026 schedule + team stat provider — LIVE knockout edition.

Real remaining Round of 16 fixtures (verified July 4, 2026). As each round
resolves, append the quarterfinal/semifinal fixtures here — kickoffs are
already known (QF: Jul 9-11, SF: Jul 14-15, Final: Jul 19 at MetLife).

Team stats are hand-calibrated from tournament form through the Round of 32.
For sharper numbers, wire get_team_stats() to FBref/Transfermarkt later.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Match:
    match_id: str
    home: str
    away: str
    group: str
    kickoff: datetime
    stage: str = "group"  # group | knockout
    venue: str = ""


def _utc(y, mo, d, h, mi=0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


_SCHEDULE: list[Match] | None = None


def load_schedule() -> list[Match]:
    """Remaining WC26 Round of 16 fixtures, kickoff times in UTC.
    (ET + 4h = UTC.) Matches whose kickoff has passed are automatically
    ignored by the scheduler and API filters.
    """
    global _SCHEDULE
    if _SCHEDULE is None:
        _SCHEDULE = [
            # Sunday July 5
            Match("BRA_NOR", "Brazil", "Norway", "R16", _utc(2026, 7, 5, 20),
                  stage="knockout", venue="MetLife Stadium, NJ"),
            Match("MEX_ENG", "Mexico", "England", "R16", _utc(2026, 7, 6, 0),
                  stage="knockout", venue="Estadio Azteca, Mexico City"),
            # Monday July 6
            Match("POR_ESP", "Portugal", "Spain", "R16", _utc(2026, 7, 6, 19),
                  stage="knockout", venue="AT&T Stadium, Arlington"),
            Match("USA_BEL", "United States", "Belgium", "R16", _utc(2026, 7, 7, 0),
                  stage="knockout", venue="Lumen Field, Seattle"),
            # Tuesday July 7
            Match("ARG_EGY", "Argentina", "Egypt", "R16", _utc(2026, 7, 7, 16),
                  stage="knockout", venue="Mercedes-Benz Stadium, Atlanta"),
            Match("SUI_COL", "Switzerland", "Colombia", "R16", _utc(2026, 7, 7, 20),
                  stage="knockout", venue="BC Place, Vancouver"),
            # --- Quarterfinals (add opponents as R16 resolves) ---
            # Jul 9 21:00? Foxborough: FRA/PAR winner vs Morocco
            # Jul 10: POR/ESP winner vs USA/BEL winner (Los Angeles)
            # Jul 11: BRA/NOR winner vs MEX/ENG winner (Miami)
            # Jul 11: ARG/EGY winner vs SUI/COL winner (Kansas City)
        ]
    return _SCHEDULE


# ---------------------------------------------------------------------------
# Team stats calibrated from WC26 tournament form (through Round of 32).
# attack/defence are relative ratings ~1.0; form 0-1 from recent results.
# ---------------------------------------------------------------------------
TEAM_STATS: dict[str, dict] = {
    # 4-0-0 feel: France cruising (3-1, 3-0, 4-1, 3-0 vs Sweden)
    "France":        {"attack": 1.62, "defence": 0.68, "form": 0.85, "set_piece_threat": 0.26, "red_card_risk": 0.05, "fatigue": 0.24, "elo": 2180},
    # Brazil solid, edged Japan 2-1 in R32
    "Brazil":        {"attack": 1.50, "defence": 0.74, "form": 0.72, "set_piece_threat": 0.28, "red_card_risk": 0.05, "fatigue": 0.22, "elo": 2130},
    # Norway: Haaland 5 of their 9 goals; beat Ivory Coast, lost 1-4 to France
    "Norway":        {"attack": 1.34, "defence": 0.92, "form": 0.66, "set_piece_threat": 0.24, "red_card_risk": 0.05, "fatigue": 0.20, "elo": 1940},
    # Mexico: hosts, 2-0 over Ecuador, strong group
    "Mexico":        {"attack": 1.22, "defence": 0.84, "form": 0.74, "set_piece_threat": 0.19, "red_card_risk": 0.06, "fatigue": 0.18, "elo": 1930},
    # England: 4-2 Croatia, ground out results since
    "England":       {"attack": 1.44, "defence": 0.78, "form": 0.70, "set_piece_threat": 0.32, "red_card_risk": 0.04, "fatigue": 0.22, "elo": 2090},
    # Portugal: 2-1 Croatia in R32, won without Ronaldo starting
    "Portugal":      {"attack": 1.48, "defence": 0.80, "form": 0.72, "set_piece_threat": 0.25, "red_card_risk": 0.06, "fatigue": 0.24, "elo": 2080},
    # Spain: 3-0 Austria, "mojo back", first KO win since 2010
    "Spain":         {"attack": 1.54, "defence": 0.70, "form": 0.80, "set_piece_threat": 0.20, "red_card_risk": 0.04, "fatigue": 0.22, "elo": 2140},
    # USA: 2-0 over Bosnia, but Balogun suspended (red card) for Belgium
    "United States": {"attack": 1.12, "defence": 0.88, "form": 0.68, "set_piece_threat": 0.21, "red_card_risk": 0.05, "fatigue": 0.18, "elo": 1900},
    # Belgium: 3-2 AET over Senegal, KDB/Lukaku vintage but leggy
    "Belgium":       {"attack": 1.38, "defence": 0.95, "form": 0.64, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.30, "elo": 2000},
    # Argentina: Messi Golden Boot leader but scraped Cape Verde 3-2 AET
    "Argentina":     {"attack": 1.55, "defence": 0.76, "form": 0.70, "set_piece_threat": 0.27, "red_card_risk": 0.06, "fatigue": 0.28, "elo": 2170},
    # Egypt: first KO advancement ever, via pens; Salah-dependent
    "Egypt":         {"attack": 1.02, "defence": 0.90, "form": 0.62, "set_piece_threat": 0.18, "red_card_risk": 0.07, "fatigue": 0.30, "elo": 1820},
    # Switzerland: 2-0 Algeria, defensively organized as ever
    "Switzerland":   {"attack": 1.18, "defence": 0.80, "form": 0.68, "set_piece_threat": 0.22, "red_card_risk": 0.05, "fatigue": 0.20, "elo": 1950},
    # Colombia: 1-0 Ghana, reshaped after bad loss to France in prep
    "Colombia":      {"attack": 1.20, "defence": 0.82, "form": 0.66, "set_piece_threat": 0.21, "red_card_risk": 0.07, "fatigue": 0.20, "elo": 1940},
    # For QFs when they resolve:
    "Morocco":       {"attack": 1.24, "defence": 0.76, "form": 0.76, "set_piece_threat": 0.20, "red_card_risk": 0.07, "fatigue": 0.24, "elo": 1990},
    "Paraguay":      {"attack": 1.00, "defence": 0.84, "form": 0.62, "set_piece_threat": 0.19, "red_card_risk": 0.08, "fatigue": 0.26, "elo": 1840},
}

_DEFAULT = {"attack": 1.1, "defence": 1.0, "form": 0.5, "set_piece_threat": 0.2,
            "red_card_risk": 0.06, "fatigue": 0.2, "elo": 1800}


def get_team_stats(team: str) -> dict:
    return dict(TEAM_STATS.get(team, _DEFAULT))


def get_match(match_id: str) -> Match | None:
    for m in load_schedule():
        if m.match_id == match_id:
            return m
    return None
