"""World Cup 2026 schedule + team stat provider — LIVE knockout edition.

FIXTURES: real remaining Round of 16 matches (verified July 4, 2026).
Hand-fed by design (auto-discovery deferred). When R16 resolves, add QFs —
the slots/venues are already known (see the comment block below).

TEAM STATS: rebuilt July 5, 2026 from real, multi-source tournament data
(replacing the original hand-typed estimates). Sources per field:

  elo      footballratings.org, "Ratings as of 4 July 2026" (live Elo table).
           USA & Egypt were below the retrieved top slice — their values are
           ESTIMATES (bounded < Belgium 1910; Egypt Jan-2026 archive ~1653),
           flagged inline. TODO(Son): grab exact values from the site.
  attack   xG-for per game from RealGM's WC26 xG tracker + FIFA official
           match stats + OddAlerts team xG table, blended with goals/game
           where per-match xG was partial.
  defence  xG-against / goals-against per game from the same sources, plus
           qualitative signals (Opta clean-sheet records, ESPN tactical
           analysis) noted inline.
  form     Last-5-official W/D/L strings from footballratings.org
           (W=1, D=0.5, L=0, simple average).
  set_piece_threat / red_card_risk / fatigue
           HONEST ESTIMATES. Only strong direct evidence is encoded:
           Argentina fatigue (Scaloni: "absolutely knackered", 120 min),
           Belgium (AET comeback), Egypt (120 min + pens), USA red-card
           risk (Balogun sent off + suspended). Everything else is a
           reasonable default, not a measured value.

CONVERSION FORMULA (tournament base xG = LEAGUE_BASE_XG = 1.30):
  attack  = clamp(xGF_per_game / 1.30, 0.75, 1.45)
  defence = clamp(0.55 + 0.45 * (xGA_per_game / 1.30), 0.62, 1.06)
            (lower = better; avg xGA -> ~1.0, elite ~0.3/g -> ~0.65)
Small documented qualitative nudges (±0.03) applied where narrative
evidence is strong (e.g. Brazil's aging back four).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


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
    """Remaining WC26 Round of 16 fixtures, kickoff times in UTC (ET+4h)."""
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
            # --- Quarterfinals: slots known, add winners as R16 resolves ---
            # Thu Jul 9  ~20:00 UTC  Morocco vs FRA/PAR winner   (Gillette, Boston)
            # Fri Jul 10 ~19:00 UTC  USA/BEL w. vs POR/ESP w.    (SoFi, Inglewood)
            # Sat Jul 11 ~21:00 UTC  BRA/NOR w. vs MEX/ENG w.    (Hard Rock, Miami)
            # Sun Jul 12 ~01:00 UTC  ARG/EGY w. vs SUI/COL w.    (Arrowhead, KC)
        ]
    return _SCHEDULE


def is_trackable(match: Match, now: datetime,
                 hours_ahead: float, hours_after: float) -> bool:
    """A match is trackable from `hours_ahead` before kickoff until
    `hours_after` past kickoff (live odds keep moving on goals; Kalshi
    books settle within a few hours of the final whistle)."""
    return (match.kickoff <= now + timedelta(hours=hours_ahead)
            and now < match.kickoff + timedelta(hours=hours_after))


# ---------------------------------------------------------------------------
# TEAM_STATS — sourced, see module docstring for methodology & sources.
# Comment format per team: xGF/g, xGA/g inputs -> formula outputs, evidence.
# ---------------------------------------------------------------------------
TEAM_STATS: dict[str, dict] = {
    # xGF~2.05 (8GF/4, 0 conceded run), xGA 0.28/g (OddAlerts: 1.1 total, best
    # in tournament). Elo #1. "Dominant" vs Austria 3-0. Form WWWDD.
    "Spain":         {"attack": 1.45, "defence": 0.65, "form": 0.80, "set_piece_threat": 0.20, "red_card_risk": 0.04, "fatigue": 0.20, "elo": 2159},
    # xGF~1.85 (Messi 7 goals), xGA~0.85 (conceded 2 to Cape Verde). 120-min
    # R32; Scaloni: squad "absolutely knackered" -> fatigue 0.34. Form WWWWL.
    "Argentina":     {"attack": 1.42, "defence": 0.84, "form": 0.80, "set_piece_threat": 0.27, "red_card_risk": 0.06, "fatigue": 0.34, "elo": 2151},
    # xGF~1.55, xGA~1.0. Fell behind early to DR Congo, Kane rescue. WWDWW.
    "England":       {"attack": 1.19, "defence": 0.88, "form": 0.90, "set_piece_threat": 0.30, "red_card_risk": 0.04, "fatigue": 0.22, "elo": 2046},
    # xGF 1.98 (6.19 group xG + 1.72 vs Japan), xGA~0.65. Formula defence 0.77
    # +0.03 qualitative: 2nd-oldest Brazil WC XI ever, ESPN flags fragility;
    # needed 96' winner vs Japan. Form WWWDL.
    "Brazil":        {"attack": 1.45, "defence": 0.80, "form": 0.70, "set_piece_threat": 0.27, "red_card_risk": 0.05, "fatigue": 0.24, "elo": 2031},
    # xG diff -0.43/g (RealGM) — results outrunning underlying quality.
    # attack blends xGF~1.2 with 2.0 goals/g finishing overperformance.
    # defence formula -> 1.06 clamp: allowing real chance quality. Won R32
    # via 68' pen (Ronaldo's 1st-ever WC KO goal) + 94' header. Form WDWDW.
    "Portugal":      {"attack": 1.17, "defence": 1.06, "form": 0.80, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.24, "elo": 2013},
    # xGF~1.15, xGA~0.45: 3 consecutive clean sheets (team record), grinding
    # 1-goal wins. Form DWWWW.
    "Colombia":      {"attack": 0.88, "defence": 0.71, "form": 0.90, "set_piece_threat": 0.22, "red_card_risk": 0.07, "fatigue": 0.20, "elo": 2004},
    # xGF~1.30 (1.41/0.48/1.79/~1.5), xGA~0.55: 0 conceded in 4 — first team
    # since 1994 (Opta). Formula defence 0.74, -0.02 qualitative (record run,
    # Azteca fortress: never lost a WC match there, W8 D2). Form WWWWW.
    "Mexico":        {"attack": 1.00, "defence": 0.72, "form": 0.95, "set_piece_threat": 0.20, "red_card_risk": 0.06, "fatigue": 0.18, "elo": 1943},
    # xGF~1.45 (9GF/4), xGA~0.95. First KO win since 1938. Form WWWDD.
    "Switzerland":   {"attack": 1.12, "defence": 0.88, "form": 0.80, "set_piece_threat": 0.22, "red_card_risk": 0.05, "fatigue": 0.20, "elo": 1943},
    # xGF~1.55 but Haaland (5 of team's goals) finishing OVER xG; Ivory Coast
    # out-created them (missed 1.75 xG). xGA~1.45 (8 GA/4 raw incl 1-4 FRA).
    # Haaland-dependent attack, genuinely leaky defence. Form WLWWW.
    "Norway":        {"attack": 1.19, "defence": 1.05, "form": 0.80, "set_piece_threat": 0.26, "red_card_risk": 0.05, "fatigue": 0.20, "elo": 1934},
    # Official xGF 2.42/90 (OddAlerts, tournament-high 9.68) inflated by 5-1
    # vs New Zealand -> damped to ~1.70. xGA~1.0. AET comeback vs Senegal:
    # old-guard legs -> fatigue 0.32. Form WWDDW.
    "Belgium":       {"attack": 1.31, "defence": 0.90, "form": 0.80, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.32, "elo": 1910},
    # xGF~1.55 formula 1.19, -0.08: Balogun (their sharpest finisher)
    # SUSPENDED for this match after R32 red card. xGA~1.35 (Türkiye put
    # 2.71 xG on them). Held a clean sheet a man down vs Bosnia (resilient).
    # ELO IS AN ESTIMATE (below Belgium 1910; FIFA rank 17). Form ~LWDWW.
    "United States": {"attack": 1.11, "defence": 1.02, "form": 0.70, "set_piece_threat": 0.21, "red_card_risk": 0.08, "fatigue": 0.20, "elo": 1855},
    # xGF~1.05 (Salah "off-colour" — Al Jazeera; wasted best chances),
    # xGA~1.0. First-ever KO advancement, via 120 min + pens -> fatigue 0.30.
    # ELO IS AN ESTIMATE (Jan-2026 archive ~1653 + solid WC run).
    "Egypt":         {"attack": 0.81, "defence": 0.90, "form": 0.60, "set_piece_threat": 0.19, "red_card_risk": 0.07, "fatigue": 0.30, "elo": 1720},
    # --- For QFs when R16 resolves (France elo sourced; others estimates) ---
    # Tournament-leading 14 goals, xGF~2.3; cruising (WWWWW). Elo sourced 2134.
    "France":        {"attack": 1.45, "defence": 0.83, "form": 0.95, "set_piece_threat": 0.25, "red_card_risk": 0.05, "fatigue": 0.18, "elo": 2134},
    # Beat Canada 3-0, eliminated Netherlands on pens. ELO ESTIMATE.
    "Morocco":       {"attack": 1.15, "defence": 0.81, "form": 0.85, "set_piece_threat": 0.21, "red_card_risk": 0.07, "fatigue": 0.24, "elo": 1935},
    # Two straight 120-min matches (pens vs Germany); minimal attack
    # (0.24-0.32 xG games). ELO ESTIMATE.
    "Paraguay":      {"attack": 0.78, "defence": 0.85, "form": 0.60, "set_piece_threat": 0.19, "red_card_risk": 0.08, "fatigue": 0.34, "elo": 1840},
}

_DEFAULT = {"attack": 1.0, "defence": 0.95, "form": 0.5, "set_piece_threat": 0.2,
            "red_card_risk": 0.06, "fatigue": 0.2, "elo": 1800}


def get_team_stats(team: str) -> dict:
    return dict(TEAM_STATS.get(team, _DEFAULT))


def get_match(match_id: str) -> Match | None:
    for m in load_schedule():
        if m.match_id == match_id:
            return m
    return None
