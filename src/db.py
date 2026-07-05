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


engine = create_engine(config.DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


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
