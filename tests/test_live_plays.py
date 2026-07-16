"""Play-by-play pattern reading: parser, momentum window, lever tilt."""
from src.live_plays import (KIND_WEIGHTS, MOMENTUM_TILT_CAP, momentum,
                            parse_plays)


def _item(secs, text):
    return {"time": {"value": secs}, "text": text}


# real ESPN/Opta commentary shapes, England vs Argentina
FEED = [
    _item(0, "First Half begins."),
    _item(120, "Foul by Leandro Paredes (Argentina)."),           # neutral
    _item(300, "Attempt saved. Harry Kane (England) right footed shot "
               "from the centre of the box is saved."),
    _item(420, "Corner, England. Conceded by Nicolás Otamendi."),
    _item(600, "Attempt missed. Lionel Messi (Argentina) left footed "
               "shot from outside the box is high."),
    _item(720, "Attempt blocked. Jude Bellingham (England) right footed "
               "shot from the centre of the box is blocked."),
    _item(900, "Offside, Argentina. Rodrigo De Paul tries a through "
               "ball, but Lionel Messi is caught offside."),
    _item(1080, "Julián Álvarez (Argentina) wins a free kick in the "
                "attacking half."),
    _item(1200, "Penalty conceded by Ezri Konsa (England) after a foul "
                "in the penalty area."),
    _item(1320, "Goal! England 1, Argentina 0. Anthony Gordon (England) "
                "header from the centre of the box."),
    _item(1500, "Substitution, England. Ezri Konsa replaces Anthony "
                "Gordon."),                                        # neutral
]


class TestParser:
    def test_kinds_and_sides(self):
        plays = parse_plays(FEED, "England", "Argentina")
        got = [(p["kind"], p["side"]) for p in plays]
        assert got == [
            ("attempt_on_target", "home"),   # Kane saved
            ("corner", "home"),
            ("attempt_off", "away"),         # Messi high
            ("attempt_blocked", "home"),     # Bellingham
            ("offside", "away"),
            ("fk_attacking", "away"),        # Álvarez
            ("penalty_won", "away"),         # conceded BY England
            ("goal", "home"),                # Gordon
        ]

    def test_weights_attached_and_neutral_items_skipped(self):
        plays = parse_plays(FEED, "England", "Argentina")
        assert all(p["weight"] == KIND_WEIGHTS[p["kind"]] for p in plays)
        assert not any("Substitution" in p["text"] or "Foul by" in p["text"]
                       for p in plays)

    def test_minutes_from_clock_seconds(self):
        plays = parse_plays(FEED, "England", "Argentina")
        assert plays[0]["minute"] == 5.0        # Kane attempt at 300s

    def test_unattributable_text_skipped(self):
        plays = parse_plays([_item(60, "Attempt saved. Someone (Brazil) "
                                       "shoots.")], "England", "Argentina")
        assert plays == []

    def test_empty_feed(self):
        assert parse_plays([], "England", "Argentina") == []
        assert parse_plays(None, "England", "Argentina") == []


class TestMomentum:
    def _p(self, minute, side, kind="attempt_on_target"):
        return {"minute": minute, "side": side, "kind": kind,
                "weight": KIND_WEIGHTS[kind], "text": ""}

    def test_pressing_side_tilts_up(self):
        # away generated ALL the recent threat; match-long share was even
        plays = [self._p(70, "away"), self._p(72, "away"),
                 self._p(74, "away"), self._p(75, "away")]
        m = momentum(plays, cum_share_home=0.5)
        assert m["recent_share_home"] < 0.3
        assert m["mult_away"] > 1.0 > m["mult_home"]
        assert m["mult_away"] <= 1.0 + MOMENTUM_TILT_CAP

    def test_pattern_matching_cumulative_is_no_tilt(self):
        plays = [self._p(70, "home"), self._p(72, "away")]
        m = momentum(plays, cum_share_home=0.5)
        assert abs(m["mult_home"] - 1.0) < 0.03

    def test_old_plays_fall_out_of_window(self):
        # a first-half barrage followed by a long quiet spell: the barrage
        # is outside the 12-min window relative to the latest play
        plays = [self._p(10, "home"), self._p(11, "home"),
                 self._p(12, "home"), self._p(40, "away")]
        m = momentum(plays, cum_share_home=0.75)
        assert m["recent_share_home"] < 0.5     # only the away play counts

    def test_no_plays_is_no_read(self):
        assert momentum([], 0.5) is None


class TestLeverBlend:
    def _stats(self, sot_h, sot_a, sh_h, sh_a):
        return {"available": True, "rows": [
            {"key": "shotsOnTarget", "home": str(sot_h), "away": str(sot_a)},
            {"key": "totalShots", "home": str(sh_h), "away": str(sh_a)}]}

    def test_momentum_shifts_attack_levers(self):
        from src.live_auto import suggest_levers
        stats = self._stats(3, 3, 8, 8)          # dead-even cumulative
        base = suggest_levers(1.3, 1.3, stats, 70)
        surge = [{"minute": m, "side": "away", "kind": "attempt_on_target",
                  "weight": 1.0, "text": ""} for m in (62, 65, 67, 69)]
        tilted = suggest_levers(1.3, 1.3, stats, 70, plays=surge)
        assert tilted["away"] > base["away"]
        assert tilted["home"] < base["home"]
        assert tilted["source"] == "live shots + plays"
        assert tilted["momentum"]["pressure_away"] > 0

    def test_no_plays_keeps_cumulative_read(self):
        from src.live_auto import suggest_levers
        stats = self._stats(4, 1, 10, 3)
        a = suggest_levers(1.3, 1.3, stats, 70)
        b = suggest_levers(1.3, 1.3, stats, 70, plays=[])
        assert (a["home"], a["away"]) == (b["home"], b["away"])
        assert b["momentum"] is None
