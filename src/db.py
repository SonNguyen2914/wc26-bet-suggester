"""Database layer. SQLAlchemy models + session helpers.

Works with SQLite out of the box; set DATABASE_URL for Postgres.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text, create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker

import config

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Prediction(Base):
    """Every model run, scheduled or on-demand, is stored here."""
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)
    market_title = Column(String(256))

    model_probability = Column(Float, nullable=False)
    kalshi_odds = Column(Float)                # decimal odds
    implied_probability = Column(Float)
    edge = Column(Float)
    expected_value = Column(Float)             # EV per $1 staked
    confidence = Column(Float)

    xg_home = Column(Float)
    xg_away = Column(Float)
    scoreline_json = Column(Text)              # top scorelines as JSON
    summary_json = Column(Text)                # match-level prediction summary:
                                               # full_time W/D/L, advance (ET/pens),
                                               # first/second-half distributions
    outcome_key = Column(String(32))           # home_win, over_2_5, score_2_1, ...

    source = Column(String(24), default="scheduled")   # scheduled | on_demand | final_lock
    is_final = Column(Boolean, default=False)
    model_version = Column(String(24), default="v1")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_pred_match_created", "match_id", "created_at"),
        Index("ix_pred_market_created", "market_id", "created_at"),
    )


class Suggestion(Base):
    """Filtered, ranked bets that passed the edge/confidence thresholds."""
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)
    market_title = Column(String(256))
    kickoff = Column(DateTime(timezone=True))

    model_probability = Column(Float)
    kalshi_odds = Column(Float)
    implied_probability = Column(Float)
    edge = Column(Float)
    expected_value = Column(Float)
    confidence = Column(Float)
    recommendation = Column(String(16))        # TAKE | SKIP
    reason = Column(Text)

    is_final = Column(Boolean, default=False)
    resolved = Column(Boolean, default=False)
    outcome_won = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_sugg_match", "match_id", "created_at"),)


class OddsReading(Base):
    """One row per market per poll (~every 30s). This is the learning corpus:
    rolling stats, percentiles, and momentum are all computed from here, and
    it becomes Prophet/ARIMA training data once enough matches accumulate."""
    __tablename__ = "odds_readings"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)

    yes_price = Column(Float, nullable=False)          # 0-1
    decimal_odds = Column(Float, nullable=False)
    model_probability = Column(Float)                  # latest cached model view
    edge = Column(Float)                               # model_p - yes_price
    volume_24h = Column(Float)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_reading_market_time", "market_id", "created_at"),)


class WatchlistItem(Base):
    """Markets the user asked to be notified about when the price is ripe."""
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False, unique=True)
    market_title = Column(String(256))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class TimingAlert(Base):
    """Fired notifications ('this bet is ripe'), also shown as an in-app feed."""
    __tablename__ = "timing_alerts"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)
    market_title = Column(String(256))

    score = Column(Float, nullable=False)              # 0-100 ripeness at fire time
    decimal_odds = Column(Float)
    edge = Column(Float)
    reasons = Column(Text)                             # human-readable breakdown
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_alert_market_time", "market_id", "created_at"),)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(64), primary_key=True)
    value = Column(String(256))


class MatchResult(Base):
    """A finished match, frozen at the moment the live feed stopped showing it
    in progress. We snapshot the final score/scorers here because a finished
    fixture drops out of API-Football's /fixtures?live=all response within a
    minute or two of the whistle — so if we don't capture it live, re-reading
    it later would cost a per-match feed call. One row per match, upserted.

    Drives two things: the ranking board excludes a match the instant a row
    exists here (bets stop persisting when the game ends, not 4h later), and
    the landing page keeps the match on the live scoreboard as an "FT" card
    for a grace window past finished_at, then moves it to Past matches.
    """
    __tablename__ = "match_results"

    match_id = Column(String(64), primary_key=True)
    home = Column(String(64))
    away = Column(String(64))
    home_goals = Column(Integer, default=0)
    away_goals = Column(Integer, default=0)
    status_short = Column(String(8))                  # FT | AET | PEN
    red_home = Column(Boolean, default=False)
    red_away = Column(Boolean, default=False)
    goals_json = Column(Text)                          # scorers snapshot as JSON
    finished_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_result_finished", "finished_at"),)


class LiveSignal(Base):
    """In-play alerts from the live read. Two kinds: 'watched' BUY/SELL —
    the live remainder-simulation diverges from the price on a market Son
    is betting; 'easy_win' — ANY open book the live model calls near-certain
    while the price still pays. Informational by design — the market knows
    the score — but these are the reads Son asked to be pinged with,
    thresholded and cooled down so they only speak when it's real."""
    __tablename__ = "live_signals"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)
    market_title = Column(String(256))
    side = Column(String(8))               # BUY | SELL
    kind = Column(String(16), default="watched")   # watched | easy_win
    live_probability = Column(Float)
    market_probability = Column(Float)
    difference = Column(Float)
    minute = Column(Float)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_signal_created", "created_at"),)


class BotPosition(Base):
    """One paper position for the strategy-lab bots: YES contracts bought at
    entry_price, cost inclusive of the modelled Kalshi fee. Open until an
    early exit (WIRE) or settlement from the closing snapshot. pnl is the
    GROSS return of the close (proceeds or payout); net = pnl - cost."""
    __tablename__ = "bot_positions"

    id = Column(Integer, primary_key=True)
    bot = Column(String(16), nullable=False)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)
    market_title = Column(String(256))
    entry_price = Column(Float, nullable=False)
    contracts = Column(Integer, nullable=False)
    cost = Column(Float, nullable=False)
    note = Column(String(256))
    opened_at = Column(DateTime(timezone=True), default=utcnow)
    closed_at = Column(DateTime(timezone=True))
    close_price = Column(Float)
    close_reason = Column(String(64))
    pnl = Column(Float)

    __table_args__ = (Index("ix_bot_positions", "bot", "match_id"),)


class MarketClosing(Base):
    """Post-match snapshot of every Kalshi market on a finished match —
    settlement result + closing book, captured once at freeze time (and
    backfillable afterwards, since Kalshi keeps settled markets queryable
    by event). The T-10 final lock preserves the MODEL's side of the
    record; this preserves the MARKET's side, so research can line up
    model probability vs closing price vs actual outcome per market.
    Raw Kalshi market object stored as JSON — no schema churn as Kalshi
    adds fields."""
    __tablename__ = "market_closings"

    id = Column(Integer, primary_key=True)
    match_id = Column(String(64), nullable=False)
    market_id = Column(String(128), nullable=False)
    event_ticker = Column(String(128))
    captured_at = Column(DateTime(timezone=True), default=utcnow)
    data_json = Column(Text)

    __table_args__ = (Index("ix_closing_match", "match_id"),)


class MatchLiveSnapshot(Base):
    """Last-seen-LIVE state per match, refreshed every poll while the match is
    in progress. The scoreboard reads from HERE, not directly from the feed,
    so it's robust to API-Football's /fixtures?live=all dropping a match during
    between-periods breaks (the 90'->ET gap, the ET->penalties gap). Without
    this, a match in extra time briefly vanishes from live=all and the card
    disappears — the bug this fixes.

    One row per match, upserted. `last_seen_at` lets the scoreboard hold a
    match through short feed gaps (grace window) and lets the poller detect a
    real finish (was live, now gone past the grace window -> freeze result).
    """
    __tablename__ = "match_live_snapshots"

    match_id = Column(String(64), primary_key=True)
    home = Column(String(64))
    away = Column(String(64))
    home_goals = Column(Integer, default=0)
    away_goals = Column(Integer, default=0)
    minutes_elapsed = Column(Float, nullable=True)
    status_short = Column(String(8))
    red_home = Column(Boolean, default=False)
    red_away = Column(Boolean, default=False)
    goals_json = Column(Text)
    last_seen_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_snap_seen", "last_seen_at"),)


engine = create_engine(config.DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Tiny forward-only migration: create_all doesn't alter existing tables,
    so if a persisted volume carries an old predictions table, add the
    outcome_key column in place instead of crashing on the first insert."""
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(predictions)"))]
        if cols and "outcome_key" not in cols:
            conn.execute(text("ALTER TABLE predictions ADD COLUMN outcome_key VARCHAR(32)"))
            conn.commit()
            print("[db] migrated: added predictions.outcome_key")
        if cols and "summary_json" not in cols:
            conn.execute(text("ALTER TABLE predictions ADD COLUMN summary_json TEXT"))
            conn.commit()
            print("[db] migrated: added predictions.summary_json")


def get_setting(session, key: str, default: float) -> float:
    row = session.get(Setting, key)
    return float(row.value) if row else default


def set_setting(session, key: str, value) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = str(value)
    else:
        session.add(Setting(key=key, value=str(value)))
    session.commit()
