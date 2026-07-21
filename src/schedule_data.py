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

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# How far ahead of kickoff a KNOCKOUT match becomes trackable. Kalshi opens
# knockout markets days early (once the bracket resolves), so we track them
# well before the group-stage default (6h) to surface prices/edge right away.
# 96h covers the ~3-4 day gap between a bracket resolving and the match.
KNOCKOUT_TRACK_HOURS_AHEAD = float(os.getenv("KNOCKOUT_TRACK_HOURS_AHEAD", "96"))


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
    # 3rd-place match: sides are filled by the LOSERS of the feeder matches,
    # not the winners (the only slot in the bracket that tracks losers).
    loser_feed: bool = False

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
            # Saturday July 4 (added retroactively so the bracket's R16 tier
            # is complete — results/settlements restore from ESPN + Kalshi)
            Match("CAN_MAR", "Canada", "Morocco", "R16", _utc(2026, 7, 4, 17),
                  stage="knockout", venue="NRG Stadium, Houston"),
            Match("PAR_FRA", "Paraguay", "France", "R16", _utc(2026, 7, 4, 21),
                  stage="knockout", venue="Lincoln Financial Field, Philadelphia"),
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
            # --- Quarterfinals: RESOLVED (seeded from confirmed R16 results,
            # July 7). All four matchups are known:
            #   Spain 1-0 Portugal, Belgium 4-1 USA  -> Spain vs Belgium
            #   Norway 2-1 Brazil, England 3-2 Mexico -> Norway vs England
            #   Argentina 3-2 Egypt, Switzerland 0-0 Colombia (pens 4-3)
            #                                         -> Argentina vs Switzerland
            # Seeded directly (zero-API) so the bracket is correct immediately;
            # the feed-fetch resolver (bracket.resolve_bracket) handles future
            # rounds (SF, final) automatically as QFs finish.
            Match("MAR_FRA", "Morocco", "France", "QF",
                  _utc(2026, 7, 9, 20), stage="knockout",
                  venue="Gillette Stadium, Boston"),
            Match("ESP_BEL", "Spain", "Belgium", "QF",
                  _utc(2026, 7, 10, 19), stage="knockout",
                  venue="SoFi Stadium, Inglewood"),
            Match("NOR_ENG", "Norway", "England", "QF",
                  _utc(2026, 7, 11, 21), stage="knockout",
                  venue="Hard Rock Stadium, Miami"),
            Match("ARG_SUI", "Argentina", "Switzerland", "QF",
                  _utc(2026, 7, 12, 1), stage="knockout",
                  venue="Arrowhead Stadium, Kansas City"),
            # --- Semifinals: seeded as placeholders, resolve as QFs finish ----
            # SF1 = winner(MAR_FRA) vs winner(ESP_BEL), Jul 14, AT&T Dallas
            # SF2 = winner(NOR_ENG) vs winner(ARG_SUI), Jul 15, Atlanta
            Match("SF1", "MAR/FRA winner", "ESP/BEL winner", "SF",
                  _utc(2026, 7, 14, 19), stage="knockout",
                  venue="AT&T Stadium, Arlington",
                  home_feeders=("MAR_FRA",), away_feeders=("ESP_BEL",),
                  home_resolved=False, away_resolved=False),
            Match("SF2", "NOR/ENG winner", "ARG/SUI winner", "SF",
                  _utc(2026, 7, 15, 19), stage="knockout",
                  venue="Mercedes-Benz Stadium, Atlanta",
                  home_feeders=("NOR_ENG",), away_feeders=("ARG_SUI",),
                  home_resolved=False, away_resolved=False),
            # --- Third-place + Final: fed by the SEMIFINALS ------------------
            # 3rd-place = the two SF LOSERS (unusual: tracks losers, handled by
            # loser_feeders). Final = the two SF winners.
            Match("THIRD", "SF1 loser", "SF2 loser", "3P",
                  _utc(2026, 7, 18, 21), stage="knockout",
                  venue="Hard Rock Stadium, Miami",
                  home_feeders=("SF1",), away_feeders=("SF2",),
                  home_resolved=False, away_resolved=False,
                  loser_feed=True),
            Match("FINAL", "SF1 winner", "SF2 winner", "F",
                  _utc(2026, 7, 19, 19), stage="knockout",
                  venue="MetLife Stadium, East Rutherford",
                  home_feeders=("SF1",), away_feeders=("SF2",),
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
            if m.home_resolved:
                return False  # already resolved — a resolved side is final
            m.home, m.home_resolved = team, True
        elif side == "away":
            if m.away_resolved:
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

    KNOCKOUT matches get a much wider pre-kickoff window: Kalshi opens
    knockout markets days in advance (as soon as the bracket resolves), so we
    start tracking them early to surface prices and edge right away instead of
    waiting until `hours_ahead` before kickoff. Group-stage matches keep the
    tighter default window.

    A placeholder slot (one side still "X/Y winner") is NOT trackable: there's
    no real team to simulate or price markets for. It becomes trackable
    automatically once the bracket resolver fills both sides.
    """
    if not match.fully_resolved:
        return False
    if match.stage == "knockout":
        hours_ahead = max(hours_ahead, KNOCKOUT_TRACK_HOURS_AHEAD)
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
    # --- Spain: WORLD CHAMPIONS. OPP-ADJUSTED through the FINAL (8 PDFs).
    # attack holds the cap deeper than ever: 2.52 xGF vs Argentina in the
    # final (adj 2.92) lifts the 8-game avg to 2.27 adj xGF/game. defence
    # 0.66->0.65 — Argentina was held to 0.07 xG across 120 minutes of a
    # World Cup FINAL (adj 0.06), the most complete suffocation in the
    # dataset; 0.29 adj xGA/game across the whole tournament. Saudi Arabia
    # group-opponent Elo remains a documented ESTIMATE (1470, anchor 1650).
    "Spain":         {"attack": 1.45, "defence": 0.65, "form": 0.90, "set_piece_threat": 0.20, "red_card_risk": 0.04, "fatigue": 0.20, "elo": 1912,
        "scouting": "CAMPEONES DEL MUNDO. The final was the thesis made flesh: 120 minutes, 2.52 xG created, 0.07 conceded — Argentina never truly touched the ball — settled by Ferran Torres in the 106th. Rodri's midfield strangled every opponent behind the tournament's best line-breaking passing; two goals conceded in EIGHT matches, 0.29 opponent-adjusted xGA per game, eight straight wins. The most complete champion of the modern era: total control, total ruthlessness, and when the game demanded patience against ten men, total calm. Second star earned — 2010's heirs delivered."},
    # --- Argentina: RUNNERS-UP. OPP-ADJUSTED through the FINAL (8 PDFs).
    # attack clings to the cap by a hair: the 0.07 xGF final blank (adj
    # 0.08) drags the 8-game avg to 1.90 adj xGF/game vs the 1.885 cap
    # threshold. defence 0.76->0.83 (2.52 xGA in the final, adj 2.17;
    # 0.81 adj/game through 8). Group-opponent Elos are documented
    # ESTIMATES (Algeria 1635, Austria 1760, Jordan 1455, Cabo Verde
    # 1470, anchor 1650).
    "Argentina":     {"attack": 1.45, "defence": 0.83, "form": 0.85, "set_piece_threat": 0.27, "red_card_risk": 0.06, "fatigue": 0.28, "elo": 1914,
        "scouting": "Runners-up, and the final was the one night the late-goal magic had no fuel: 0.07 xG in 120 minutes against Spain's press, a red card in extra time, and no 84th-minute miracle left in the tank — the three-match pattern of decisive late blows (Egypt, Switzerland, England) died where it mattered most. Through the tournament the machine was still elite: Messi's 8 goals, the attack at the model's cap through eight matches, set pieces and crosses the signature weapon. But the closing read is honest — the deep-tournament defence leaked (0.81 adj xGA/game after the final's 2.52) and the title went to the side that never needed rescuing."},
    # --- England: THIRD PLACE. OPP-ADJUSTED through 8 PDFs. attack returns
    # to the 1.45 cap: 2.34 xGF in the 6-4 third-place win (adj 2.73)
    # lifts the 8-game avg to 1.96. defence 0.88->0.95 (2.99 xGA vs
    # France, adj 2.56; 1.14 adj/game) — the exhibition-chaos skew is
    # real and noted: the number describes the tournament ledger, not a
    # competitive-stakes defence. Full 8-game recompute matches the
    # incremental fold to 4 decimals. Group-opponent Elos are documented
    # ESTIMATES (Croatia 1810, Ghana 1590, Panama 1565, Congo DR 1525).
    "England":       {"attack": 1.45, "defence": 0.95, "form": 0.90, "set_piece_threat": 0.30, "red_card_risk": 0.04, "fatigue": 0.22, "elo": 1871,
        "scouting": "Third place, won by winning the maddest match of the tournament 6-4 — a ten-goal shootout with France where Saka finished with a hat-trick (37', 46', 87') after Rice and Konsa struck early. The tournament pattern completed itself: transition brilliance through Bellingham, Kane and Saka whenever space existed, defensive abandon whenever it didn't matter — 2.99 xG conceded in an exhibition tells you about the party, not the project. The honest ledger: elite attack (back at the model's cap through eight), a defence that had exactly one truly bad competitive night, and the SF exit to a 90+2 header as the only wound that counts."},
    # --- Brazil: eliminated (lost to Norway R16). Kept for reference/opponent calc.
    "Brazil":        {"attack": 1.45, "defence": 0.80, "form": 0.70, "set_piece_threat": 0.27, "red_card_risk": 0.05, "fatigue": 0.24, "elo": 1805,
        "scouting": "Eliminated by Norway in the R16."},
    # --- Portugal: eliminated (lost to Spain R16). Reference only.
    "Portugal":      {"attack": 1.17, "defence": 1.06, "form": 0.80, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.24, "elo": 1788,
        "scouting": "Eliminated by Spain in the R16."},
    # --- Colombia: eliminated (lost to Switzerland R16 on penalties). Reference only.
    "Colombia":      {"attack": 1.17, "defence": 0.80, "form": 0.90, "set_piece_threat": 0.22, "red_card_risk": 0.07, "fatigue": 0.18, "elo": 1740,
        "scouting": "Eliminated by Switzerland in the R16 (0-0, lost on penalties)."},
    # --- Mexico: eliminated (lost to England R16). Reference only.
    "Mexico":        {"attack": 1.00, "defence": 0.72, "form": 0.95, "set_piece_threat": 0.20, "red_card_risk": 0.06, "fatigue": 0.18, "elo": 1754,
        "scouting": "Eliminated by England in the R16."},
    # --- Switzerland: OPP-ADJUSTED. Strong process, wasteful finishing (nudged).
    # fatigue 0.18->0.30 (July 8): played 120 min + penalties in the R16 vs
    # Colombia — same ET/pens fatigue treatment as Argentina/Belgium/Egypt.
    "Switzerland":   {"attack": 1.38, "defence": 0.85, "form": 0.80, "set_piece_threat": 0.22, "red_card_risk": 0.05, "fatigue": 0.30, "elo": 1696,
        "scouting": "Process-strong but finish-wasteful \u2014 piled up 3.14 xG against Qatar and only drew, a theme the R16 hammered home: just 0.58 xG and out-shot 15-7 by Colombia in a 0-0, then through on penalties. Xhaka and Akanji anchor a possession game that drops into a deep, resilient low block against better teams (40% of the ball against Algeria), with Kobel reliable in goal. Hard to break down; the question is always whether the chances go in. No longer fresh \u2014 120 minutes plus a shootout in the R16 put real miles in the legs."},
    # --- Norway: OPP-ADJUSTED. Clinical Haaland attack, genuinely leaky defence.
    "Norway":        {"attack": 1.30, "defence": 1.06, "form": 0.80, "set_piece_threat": 0.26, "red_card_risk": 0.05, "fatigue": 0.20, "elo": 1651,
        "scouting": "Haaland is the whole plan \u2014 and it works. Beat Brazil and Senegal despite being out-created in both; the finishing is clinical, the defence genuinely leaky (conceded 2.05 xG to Senegal, 2.57 to Brazil). Direct, vertical and transition-hungry, with real set-piece height from \u00d8stig\u00e5rd and Haaland. Rides its keeper and its striker. Underrated by the rating after beating Brazil, but the back line can be got at."},
    # --- Belgium: OPP-ADJUSTED (3 PDFs, 5-1 NZL blowout excluded). attack
    # 0.99: BLUNT vs organised blocks (0-0 Iran, 1-1 Egypt) but clinical on
    # the counter (4-1 USA from 40% poss). defence 0.77 (Courtois elite).
    # AET vs Senegal -> fatigue 0.32 (old guard). De Bruyne now a sub.
    "Belgium":       {"attack": 0.99, "defence": 0.77, "form": 0.80, "set_piece_threat": 0.24, "red_card_risk": 0.06, "fatigue": 0.32, "elo": 1778,
        "scouting": "Out at the quarterfinal — 2-1 to Spain, and the whole tournament in one night: genuinely dangerous in transition (De Ketelaere's counter was the only goal Spain conceded all tournament) but unable to live with elite possession, and undone by an 88th-minute winner. Star names, streaky output; Courtois kept them in games; the old-guard legs finally ran out against the best passing side left."},
    # --- United States: eliminated (lost to Belgium R16). Reference only.
    "United States": {"attack": 1.11, "defence": 1.02, "form": 0.70, "set_piece_threat": 0.21, "red_card_risk": 0.08, "fatigue": 0.20, "elo": 1690,
        "scouting": "Eliminated by Belgium in the R16."},
    # --- Egypt: eliminated (lost to Argentina R16). Reference only.
    "Egypt":         {"attack": 0.88, "defence": 0.97, "form": 0.60, "set_piece_threat": 0.21, "red_card_risk": 0.07, "fatigue": 0.30, "elo": 1597,
        "scouting": "Eliminated by Argentina in the R16 (lost 3-2)."},
    # ===== QF teams still pending deep review =====
    # --- France: FOURTH. OPP-ADJUSTED through 8 PDFs. Attack secure at the
    # cap again: 2.99 xGF in the third-place game (adj 3.39, their best
    # single-match creation of the tournament) lifts the 8-game avg to
    # 2.19. defence 0.80->0.86 (2.34 xGA vs England, adj 2.06; 0.89
    # adj/game) — same exhibition-chaos caveat as England's number.
    # Incremental folds from the committed basis throughout (SEN/IRQ/SWE
    # group Elos remain undocumented estimates).
    "France":        {"attack": 1.45, "defence": 0.86, "form": 0.95, "set_piece_threat": 0.25, "red_card_risk": 0.05, "fatigue": 0.18, "elo": 1926,
        "scouting": "Fourth, in the strangest way possible: out-created England 2.99 to 2.34 in the third-place game and still lost it 4-6 — Mbappé twice and Barcola scored, but every French mistake was punished at maximum price. The tournament read stands: the most dangerous transition attack in the field (Mbappé finished with 10 goals across the month, the tournament's top scorer), elite defensive numbers until the last two matches, and an ending — 0.48 xG managed against Spain, then carnival defending against England — that says the legs and the focus emptied together. The talent travels to 2030 intact; the question is everything around it."},
    # --- Morocco: OPP-ADJUSTED (3 PDFs). attack raw 0.86 nudged to 1.05:
    # out-CREATED Brazil (xG 1.33-0.99) and finishing beats xG (3 goals/0.85
    # vs Canada). Genuine dark horse, not just a bus-park. Set-piece threat +.
    "Morocco":       {"attack": 1.05, "defence": 0.80, "form": 0.85, "set_piece_threat": 0.26, "red_card_risk": 0.07, "fatigue": 0.22, "elo": 1804,
        "scouting": "Out at the quarterfinal, and the ceiling was always creation: 0-2 to France with 0.16 xG generated against 3.52 conceded — Morocco out-passed France and never threatened. Still a historic run built on a disciplined mid-block, Bounou's goalkeeping and Hakimi/Saïbari counters; they leave having allowed the fewest clear chances of any eliminated side."},
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


def effective_team_stats(team: str) -> dict:
    """get_team_stats + codified, traceable adjustments from SETTLED facts.

    ET-fatigue rule: a team whose most recent finished match went to extra
    time or penalties carries fatigue >= 0.30 — the same treatment applied
    by hand to Argentina/Belgium/Egypt/Switzerland after their 120-minute
    matches (sourced pattern, now automatic for future rounds). Reads the
    frozen MatchResult store; degrades silently to the hand-set values when
    no result is recorded (e.g. right after a redeploy wipes the DB)."""
    stats = get_team_stats(team)
    try:
        from src.db import MatchResult, SessionLocal
        with SessionLocal() as s:
            last = (s.query(MatchResult)
                    .filter((MatchResult.home == team) |
                            (MatchResult.away == team))
                    .order_by(MatchResult.finished_at.desc())
                    .first())
        if last is not None and (last.status_short or "") in ("AET", "PEN") \
                and stats.get("fatigue", 0) < 0.30:
            stats["fatigue"] = 0.30
    except Exception:
        pass
    return stats
