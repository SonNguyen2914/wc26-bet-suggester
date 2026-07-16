"""Play-by-play pattern reading for the live model.

ESPN's match summary carries an Opta-style commentary feed: every attempt
(saved/blocked/missed), corner, penalty, attacking free kick — typed, with
the match clock and the team in the text. The cumulative stats the levers
already use (shot share, volume) smooth momentum away: a team that had a
great first half but is now pinned in its own box still "leads the match"
on aggregates. This module reads the RECENT pattern instead:

  plays     parse the commentary into typed, weighted threat events
  momentum  exponentially-decayed threat pressure over the last ~12 match
            minutes -> who is attacking NOW and how hard, vs the match-long
            share the aggregate levers already encode

The output is a bounded TILT on the attack levers (suggest_levers blends
it), never a replacement: recent pressure is noisy and mean-reverting, so
its say is capped well below the cumulative signals'.
"""
from __future__ import annotations

import math
import re

# threat weight per event kind — relative chance quality, not xG: an
# on-target attempt is the anchor, a corner is a fraction of one.
KIND_WEIGHTS = {
    "goal": 1.2,            # pattern-wise a goal is also peak pressure
    "attempt_on_target": 1.0,
    "attempt_blocked": 0.6,
    "attempt_off": 0.4,
    "penalty_won": 1.5,
    "corner": 0.25,
    "fk_attacking": 0.15,
    "offside": 0.1,         # caught behind the line = attacking intent
}

MOMENTUM_WINDOW_MIN = 12.0   # how far back "now" looks
MOMENTUM_HALF_LIFE = 6.0     # minutes; a 6-min-old play has half the say
MOMENTUM_TILT_CAP = 0.12     # attack-lever tilt bounded to +-12%
MOMENTUM_LAMBDA = 0.5        # tilt per unit of (recent - cumulative) share

# (regex, kind) — first match wins; all case-sensitive like the feed
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^Goal!"), "goal"),
    (re.compile(r"^Attempt saved"), "attempt_on_target"),
    (re.compile(r"^Attempt blocked"), "attempt_blocked"),
    (re.compile(r"^Attempt missed"), "attempt_off"),
    (re.compile(r"hits the (crossbar|bar|(left |right )?post)"), "attempt_on_target"),
    (re.compile(r"^Corner,"), "corner"),
    (re.compile(r"^Penalty conceded by"), "penalty_conceded"),   # flips team
    (re.compile(r"wins a penalty"), "penalty_won"),
    (re.compile(r"wins a free kick in the attacking half"), "fk_attacking"),
    (re.compile(r"^Offside,"), "offside"),
]

_PAREN_TEAM = re.compile(r"\(([^)]+)\)")
_LEAD_TEAM = re.compile(r"^(?:Corner|Offside),\s*([^.]+?)\s*\.")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _team_of(text: str, kind: str, home: str, away: str) -> str | None:
    """Which side a play credits. Commentary names the team either in
    parentheses after the player or straight after 'Corner,'/'Offside,'.
    A conceded penalty credits the OTHER side."""
    nh, na = _norm(home), _norm(away)
    m = _LEAD_TEAM.match(text)
    cands = [m.group(1)] if m else []
    cands += _PAREN_TEAM.findall(text)
    for c in cands:
        nc = _norm(c)
        if nc == nh or nh in nc:
            return "away" if kind == "penalty_conceded" else "home"
        if nc == na or na in nc:
            return "home" if kind == "penalty_conceded" else "away"
    return None


def parse_plays(commentary: list[dict], home: str, away: str) -> list[dict]:
    """Typed threat events from the raw commentary list, oldest first:
    [{minute, side ('home'|'away'), kind, weight, text}]. Unknown or
    neutral items (fouls, subs, VAR chatter) are simply skipped."""
    plays: list[dict] = []
    for item in commentary or []:
        text = item.get("text") or ""
        secs = ((item.get("time") or {}).get("value")) or 0.0
        for pat, kind in _PATTERNS:
            if pat.search(text):
                side = _team_of(text, kind, home, away)
                if side is None:
                    break
                k = "penalty_won" if kind == "penalty_conceded" else kind
                plays.append({
                    "minute": round(float(secs) / 60.0, 1),
                    "side": side,
                    "kind": k,
                    "weight": KIND_WEIGHTS[k],
                    "text": text[:160],
                })
                break
    plays.sort(key=lambda p: p["minute"])
    return plays


def momentum(plays: list[dict], cum_share_home: float) -> dict | None:
    """The recent-pattern read: decayed threat pressure per side over the
    last MOMENTUM_WINDOW_MIN of play, compared against the match-long
    share -> a bounded multiplier pair for the attack levers.

    Returns None when there's nothing recent to read (no plays yet, long
    stoppage) — the caller keeps the cumulative levers untouched."""
    if not plays:
        return None
    now = max(p["minute"] for p in plays)
    recent = [p for p in plays if now - p["minute"] <= MOMENTUM_WINDOW_MIN]
    if not recent:
        return None
    ph = pa = 0.0
    for p in recent:
        decay = math.exp(-(now - p["minute"]) * math.log(2) / MOMENTUM_HALF_LIFE)
        if p["side"] == "home":
            ph += p["weight"] * decay
        else:
            pa += p["weight"] * decay
    # +0.5 smoothing so one lone corner can't read as 100% pressure
    share_home = (ph + 0.5) / (ph + pa + 1.0)
    delta = share_home - cum_share_home
    tilt = max(-MOMENTUM_TILT_CAP, min(MOMENTUM_TILT_CAP,
                                       MOMENTUM_LAMBDA * delta))
    return {
        "recent_share_home": round(share_home, 3),
        "pressure_home": round(ph, 2),
        "pressure_away": round(pa, 2),
        "window_min": MOMENTUM_WINDOW_MIN,
        "as_of_minute": now,
        "mult_home": round(1.0 + tilt, 3),
        "mult_away": round(1.0 - tilt, 3),
    }
