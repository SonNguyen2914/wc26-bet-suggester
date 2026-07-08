"""World Cup 2026 schedule + team stat provider — LIVE knockout edition.

FIXTURES: real remaining Round of 16 matches (verified July 4, 2026).
Hand-fed by design (auto-discovery deferred). When R16 resolves, add QFs —
the slots/venues are already known (see the comment block below).

TEAM STATS: rebuilt July 5, 2026 from real, multi-source tournament data
(replacing the original hand-typed estimates). Sources per field:

  elo      footballratings.org live Elo table, "as of ~7 July 2026" (rescaled
           July 7 from Son's screenshots). NOTE: earlier values were on an
           inflated scale (top teams ~2000-2160); the whole table was moved to
           the footballratings scale (top teams ~1900) so every matchup's Elo
           DIFFERENCE is scale-consistent. USA & Egypt are now SOURCED (no
           longer estimates). Eliminated teams kept for opponent reference.
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

OPPONENT-ADJUSTED (July 7): attack/defence for Argentina, Egypt, Colombia,
Switzerland and Norway were recomputed from FIFA Post-Match PDF per-game xG,
weighted by opponent Elo (xGF vs a strong defence counts up; xGA vs a strong
attack is forgiven), then run through the formula above. Anchor opponent =
Elo 1650. ALL 10 still-alive teams (Argentina, Egypt, Colombia, Switzerland, Norway,
France, Morocco, Spain, England, Belgium) are now opponent-adjusted from
FIFA PDFs (33/45 processed July 7; 2-5 matches each, blowouts like Belgium
5-1 NZL excluded). Eliminated teams keep prior values for opponent reference.
Elo already carries overall strength, so opponent-adjustment only shapes the
attack/defence SPLIT — it does not re-rank teams.

KEY CORRECTIONS from the PDF work: Colombia attack 0.88->1.17 (out-created
Portugal, 3 clean sheets); Belgium attack 1.31->0.99 (blunt vs low blocks,
5-1 NZL inflated the raw); Morocco nudged up (out-created Brazil, clinical);
Egypt confirmed weakest (Iran out-created them); Norway leaky (worst-clamp
defence); Spain elite-D but modest finishing vs good teams.

"scouting" field: a brief, honest "how they play" blurb per team, surfaced
on the match-detail page as a read aid for the bettor. NOT a model input —
it never touches probabilities.
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
    # --- bracket auto-resolution (QF+ only) ------------------------------
    # A QF slot is created with placeholder team names ("FRA/PAR winner")
    # BEFORE its feeder matches finish. `home_feeders`/`away_feeders` name
    # the match_ids whose winner fills each side; the bracket resolver swaps
    # in the real team the moment that feeder is decided. `home_resolved`/
    # `away_resolved` track which sides are still placeholders.
    home_feeders: tuple[str, ...] = ()
    away_feeders: tuple[str, ...] = ()
    home_resolved: bool = True
    away_resolved: bool = True

    @property
    def fully_resolved(self) -> bool:
        return self.home_resolved and self.away_resolved

    @property
    def display_home(self) -> str:
        """Real team once known, else the human placeholder label."""
        return self.home

    @property
    def display_away(self) -> str:
        return self.away


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
            # --- Quarterfinals: slots seeded with placeholders --------------
            # These exist BEFORE their feeders finish so the bracket shows
            # immediately; the bracket resolver (jobs.scheduler.resolve_bracket)
            # swaps placeholders for real teams as R16 results land. Feeder
            # match_ids map each side to the R16 fixture whose winner fills it.
            # QF1 fully known: Morocco beat Canada, France beat Paraguay 1-0
            # (Jul 4) — both feeders already resolved before this schedule was
            # seeded, so no placeholders here.
            Match("MAR_FRA", "Morocco", "France", "QF",
                  _utc(2026, 7, 9, 20), stage="knockout",
                  venue="Gillette Stadium, Boston"),
            Match("QF2", "USA/BEL winner", "POR/ESP winner", "QF",
                  _utc(2026, 7, 10, 19), stage="knockout",
                  venue="SoFi Stadium, Inglewood",
                  home_feeders=("USA_BEL",), away_feeders=("POR_ESP",),
                  home_resolved=False, away_resolved=False),
            Match("QF3", "BRA/NOR winner", "MEX/ENG winner", "QF",
                  _utc(2026, 7, 11, 21), stage="knockout",
                  venue="Hard Rock Stadium, Miami",
                  home_feeders=("BRA_NOR",), away_feeders=("MEX_ENG",),
                  home_resolved=False, away_resolved=False),
            Match("QF4", "ARG/EGY winner", "SUI/COL winner", "QF",
                  _utc(2026, 7, 12, 1), stage="knockout",
                  venue="Arrowhead Stadium, Kansas City",
                  home_feeders=("ARG_EGY",), away_feeders=("SUI_COL",),
                  home_resolved=False, away_resolved=False),
        ]
    return _SCHEDULE


def resolve_side(match_id: str, side: str, team: str) -> bool:
    """Fill one side ('home'/'away') of a QF slot with a real team once its
    feeder is decided. Idempotent: returns True only if this call actually
    changed something (so the caller can log/alert exactly once). Also
    rewrites the match_id from placeholder to real codes once BOTH sides are
    known, keeping ids stable and readable (e.g. QF1 -> MAR_FRA).
    """
    for m in load_schedule():
        if m.match_id != match_id:
            continue
        if side == "home":
            if m.home_resolved and m.home == team:
                return False
            m.home, m.home_resolved = team, True
        elif side == "away":
            if m.away_resolved and m.away == team:
                return False
            m.away, m.away_resolved = team, True
        else:
            return False
        return True
    return False


def provisional_teams() -> list[str]:
    """Resolved QF teams that have NO sourced TEAM_STATS entry yet — the model
    is running them on _DEFAULT, which the UI should flag as provisional."""
    out: list[str] = []
    for m in load_schedule():
        for resolved, name in ((m.home_resolved, m.home),
                               (m.away_resolved, m.away)):
            if resolved and name not in TEAM_STATS and name not in out:
                out.append(name)
    return out


def has_sourced_stats(team: str) -> bool:
    return team in TEAM_STATS


def is_trackable(match: Match, now: datetime,
                 hours_ahead: float, hours_after: float) -> bool:
    """A match is trackable from `hours_ahead` before kickoff until
    `hours_after` past kickoff (live odds keep moving on goals; Kalshi
    books settle within a few hours of the final whistle).

    A placeholder QF slot (one side still "X/Y winner") is NOT trackable:
    there's no real team to simulate or price markets for. It becomes
    trackable automatically once the bracket resolver fills both sides.
    """
    if not match.fully_resolved:
        return False
    return (match.kickoff <= now + timedelta(hours=hours_ahead)
            and now < match.kickoff + timedelta(hours=hours_after))


# ---------------------------------------------------------------------------
# TEAM_STATS — sourced, see module docstring for methodology & sources.
# Comment format per team: xGF/g, xGA/g inputs -> formula outputs, evidence.
# ---------------------------------------------------------------------------
TEAM_STATS: dict[str, dict] = {
    # ===== OPPONENT-ADJUSTED from FIFA Post-Match PDFs (July 7) =====
    # These 5 teams' attack/defence were recomputed from per-match xG weighted
    # by opponent Elo (see docstring). "scouting" = match-page blurb.
    # --- Spain: OPP-ADJUSTED (2 key PDFs + group). attack 1.15: dominates
    # possession/creation but converts modestly vs good teams (1-0 URU/POR,
    # 0-0 Cabo Verde); inflated to 4-0/3-0 only vs weak. defence 0.67 elite
    # (0.22 xGA vs Uruguay, best in tournament, Unai Simón).
    "Spain":         {"attack": 1.15, "defence": 0.67, "form": 0.80, "set_piece_threat": 0.20, "red_card_risk": 0.04, "fatigue": 0.20, "elo": 1912,
        "scouting": "Total control and the meanest defence in the tournament. Rodri, Pedri and Laporte suffocate games (routinely 57%+ possession), and Spain concede almost nothing — just 0.22 xG allowed against Uruguay. The catch is conversion: they dominate the ball but win tight (a run of 1-0s against good sides, a 0-0 with Cabo Verde), only running up scores against weak opponents. Elite spine, question marks over ruthlessness."},
    # --- Argentina: opp-adjusted. Elite Messi attack (caps 1.45), softer at back.
    "Argentina":     {"attack": 1.45, "defence": 0.79, "form": 0.80, "set_piece_threat": 0.27, "red_card_risk": 0.06, "fatigue": 0.34, "elo": 1914,
        "scouting": "Messi-driven elite attack \u2014 the highest chance quality of any side left, lethal from set pieces and free-kicks. Flexible shape: sits around 44-48% possession and counters against good teams, dominates the ball against weak ones. Soft at the back (conceded twice to Cabo Verde) and legs are heavy after a 120-minute R32."},
    # --- England: OPP-ADJUSTED (2 PDFs). attack 1.35 clinical (3 goals/6
    # shots vs Mexico); defence 1.0 leaky when pinned (2.01 xGA Mexico, 2 vs
    # Croatia). Pragmatic: ceded 68% possession to Mexico, won on the counter.
    "England":       {"attack": 1.35, "defence": 1.00, "form": 0.90, "set_piece_threat": 0.30, "red_card_risk": 0.04, "fatigue": 0.22, "elo": 1871,
        "scouting": "Pragmatic and ruthless in transition. Against Mexico, England ceded 68% of the ball, sat in a deep block and still won 3-2 on six shots — clinical through Bellingham, Kane and the pace of Saka and Gordon. They can play open too (4-2 over Croatia). Real set-piece threat via Kane. The worry is defensive: they get pinned and leak chances (2.01 xG allowed to Mexico), and they've fallen behind more than once before rallying."},
    # --- Brazil: eliminated (lost to Norway R16). Kept for reference/opponent calc.
    "Brazil":        {"attack": 1.45, "defence": 0.80, "form": 0.70, "set_piece_threat": 0.27, "red_card_risk": 0.05, "fatigue": 0.24, "elo": 1805,
        "scouting": "Eliminated by Norway in the R16."},
    # --- Portugal: eliminated (lost to Spain R16). Reference only.
    "Portugal":      {"attack": 1.17, "defence": 1.06, "form": 0.80, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.24, "elo": 1788,
        "scouting": "Eliminated by Spain in the R16."},
    # --- Colombia: OPP-ADJUSTED. Big upward correction (out-created Portugal).
    "Colombia":      {"attack": 1.17, "defence": 0.80, "form": 0.90, "set_piece_threat": 0.22, "red_card_risk": 0.07, "fatigue": 0.18, "elo": 1740,
        "scouting": "Quietly one of the strongest sides left \u2014 out-created Portugal and kept three clean sheets, with an elite raw xGA around 0.44. Patient 4-1-2-3 possession through D\u00edaz, James and Arias, a controlled mid-block, and excellent goalkeeping from Vargas. The one flaw is finishing: dominates xG but converts modestly. Fresh \u2014 the only quarter-finalist yet to play extra time."},
    # --- Mexico: eliminated (lost to England R16). Reference only.
    "Mexico":        {"attack": 1.00, "defence": 0.72, "form": 0.95, "set_piece_threat": 0.20, "red_card_risk": 0.06, "fatigue": 0.18, "elo": 1754,
        "scouting": "Eliminated by England in the R16."},
    # --- Switzerland: OPP-ADJUSTED. Strong process, wasteful finishing (nudged).
    "Switzerland":   {"attack": 1.38, "defence": 0.85, "form": 0.80, "set_piece_threat": 0.22, "red_card_risk": 0.05, "fatigue": 0.18, "elo": 1696,
        "scouting": "Process-strong but finish-wasteful \u2014 piled up 3.14 xG against Qatar and only drew, a recurring theme. Xhaka and Akanji anchor a possession game that flips to a deep low block against better teams (just 40% of the ball against Algeria), with Kobel reliable in goal. Got out-shot by Canada and won on finishing. Adaptable and defensively sound; the question is whether the chances go in. Fresh, no extra time."},
    # --- Norway: OPP-ADJUSTED. Clinical Haaland attack, genuinely leaky defence.
    "Norway":        {"attack": 1.30, "defence": 1.06, "form": 0.80, "set_piece_threat": 0.26, "red_card_risk": 0.05, "fatigue": 0.20, "elo": 1651,
        "scouting": "Haaland is the whole plan \u2014 and it works. Beat Brazil and Senegal despite being out-created in both; the finishing is clinical, the defence genuinely leaky (conceded 2.05 xG to Senegal, 2.57 to Brazil). Direct, vertical and transition-hungry, with real set-piece height from \u00d8stig\u00e5rd and Haaland. Rides its keeper and its striker. Underrated by the rating after beating Brazil, but the back line can be got at."},
    # --- Belgium: OPP-ADJUSTED (3 PDFs, 5-1 NZL blowout excluded). attack
    # 0.99: BLUNT vs organised blocks (0-0 Iran, 1-1 Egypt) but clinical on
    # the counter (4-1 USA from 40% poss). defence 0.77 (Courtois elite).
    # AET vs Senegal -> fatigue 0.32 (old guard). De Bruyne now a sub.
    "Belgium":       {"attack": 0.99, "defence": 0.77, "form": 0.80, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.32, "elo": 1778,
        "scouting": "Star names, streaky output. Belgium have the talent — De Bruyne (now often a sub), Doku, Trossard, De Ketelaere, Lukaku — and Courtois is elite in goal, but they stall against organised defences (a 0-0 with Iran, a 1-1 with Egypt) and look far better countering: they beat the USA 4-1 on just 40% of the ball, sitting deep and striking clinically. Old-guard legs after an extra-time R32. Dangerous in transition, blunt when they have to break a team down."},
    # --- United States: eliminated (lost to Belgium R16). Reference only.
    "United States": {"attack": 1.11, "defence": 1.02, "form": 0.70, "set_piece_threat": 0.21, "red_card_risk": 0.08, "fatigue": 0.20, "elo": 1690,
        "scouting": "Eliminated by Belgium in the R16."},
    # --- Egypt: OPP-ADJUSTED. Weakest QF side; draws flatter the numbers.
    "Egypt":         {"attack": 0.88, "defence": 0.97, "form": 0.60, "set_piece_threat": 0.21, "red_card_risk": 0.07, "fatigue": 0.30, "elo": 1597,
        "scouting": "The deepest-sitting side left \u2014 a compact low block that soaks pressure and springs Salah on the counter. The results (draws with Belgium and Iran) flatter the underlying numbers: Iran badly out-created them. Individual threat through Salah and Marmoush plus a set-piece header outlet, but a negative xG difference overall. Tired legs after a penalty-shootout R32."},
    # ===== QF teams still pending deep review =====
    # --- France: OPP-ADJUSTED (5 PDFs). Elite attack (caps), elite defence.
    "France":        {"attack": 1.45, "defence": 0.75, "form": 0.95, "set_piece_threat": 0.25, "red_card_risk": 0.05, "fatigue": 0.18, "elo": 1926,
        "scouting": "The best all-round side left. Tournament-leading attack (14 goals) built on Mbappé and devastating vertical transitions — France are happy without the ball (often under 50% possession) and lethal in space. Elite defence too: Maignan behind Saliba, Upamecano and Koundé conceded barely anything. The one wrinkle: a disciplined deep block can frustrate them (only 1-0 past a parked-bus Paraguay), which is exactly how Morocco defends. Fresh, no extra time."},
    # --- Morocco: OPP-ADJUSTED (3 PDFs). attack raw 0.86 nudged to 1.05:
    # out-CREATED Brazil (xG 1.33-0.99) and finishing beats xG (3 goals/0.85
    # vs Canada). Genuine dark horse, not just a bus-park. Set-piece threat +.
    "Morocco":       {"attack": 1.05, "defence": 0.80, "form": 0.85, "set_piece_threat": 0.26, "red_card_risk": 0.07, "fatigue": 0.22, "elo": 1804,
        "scouting": "The tournament's dark horse \u2014 far more than a defensive side. Morocco out-created Brazil (out-xG'd them in a 1-1 draw) and finish clinically (three goals off 0.85 xG against Canada). A disciplined mid-block springs Hakimi, D\u00edaz, Sa\u00efbari and Ounahi on the break, Bounou is world-class in goal, and they carry a real set-piece threat (Ounahi free-kicks, high dead-ball volume). Won their R16 in 90 minutes \u2014 fresher than sides that went to extra time."},
    # --- Paraguay: ELIMINATED (lost to France R16). Rescaled + kept for reference.
    "Paraguay":      {"attack": 0.78, "defence": 0.85, "form": 0.60, "set_piece_threat": 0.19, "red_card_risk": 0.08, "fatigue": 0.34, "elo": 1555,
        "scouting": "Eliminated by France in the R16."},
}

# _DEFAULT elo on the NEW footballratings scale (~1620 = honest mid/unknown,
# was 1800 on the old inflated scale). Used for any team without an entry.
_DEFAULT = {"attack": 1.0, "defence": 0.95, "form": 0.5, "set_piece_threat": 0.2,
            "red_card_risk": 0.06, "fatigue": 0.2, "elo": 1620,
            "scouting": ""}


def get_team_stats(team: str) -> dict:
    return dict(TEAM_STATS.get(team, _DEFAULT))


def get_match(match_id: str) -> Match | None:
    for m in load_schedule():
        if m.match_id == match_id:
            return m
    return None
