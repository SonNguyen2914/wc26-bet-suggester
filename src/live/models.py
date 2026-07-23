"""Live-plane schema, tranche 1: identity + the evidence chain
(launch decision Jul 23, minimum-schema section).

Design rules carried from the decision doc:
  - prediction batches have explicit UUIDs and status gating — readers
    only ever see status='complete'; NO time-window reconstruction;
  - exactly one canonical complete T-10 run per fixture, enforced by a
    PARTIAL UNIQUE INDEX (postgresql_where + sqlite_where so the test
    suite enforces the same invariant the production database does);
  - market prices are integer CENTS (fixed point), both sides with
    sizes, plus depth levels;
  - fixture rescheduling creates history rows, never silent overwrite;
  - fuzzy matching may PROPOSE an identity mapping; only an APPROVED
    alias row may attach a market to a fixture.

Tranche 2 (with the ingestion build): player, player_team_membership,
team/player/availability/lineup snapshots, signal, paper_position,
paper_fill, settlement.
"""
from __future__ import annotations

import uuid

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, text)
from sqlalchemy.orm import declarative_base

LiveBase = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


class Competition(LiveBase):
    __tablename__ = "competition"
    slug = Column(String(32), primary_key=True)        # mls-2026
    name = Column(String(64), nullable=False)
    provider_league_id = Column(Integer)               # API-Football id
    season = Column(Integer, nullable=False)
    timezone = Column(String(32), default="UTC")
    match_duration_minutes = Column(Integer, default=90)
    supports_draw = Column(Boolean, default=True)
    regular_time_only = Column(Boolean, default=True)
    has_group_stage = Column(Boolean, default=False)
    has_knockout_stage = Column(Boolean, default=False)
    model_version = Column(String(48))


class Team(LiveBase):
    __tablename__ = "team"
    id = Column(Integer, primary_key=True)
    competition_slug = Column(String(32),
                              ForeignKey("competition.slug"),
                              nullable=False)
    canonical_name = Column(String(80), nullable=False)
    abbrev = Column(String(8))
    espn_id = Column(String(16))
    api_football_id = Column(Integer)
    kalshi_name = Column(String(80))
    __table_args__ = (
        UniqueConstraint("competition_slug", "canonical_name"),
    )


class TeamAlias(LiveBase):
    """Identity bridge. Fuzzy matching can only PROPOSE (approved=False);
    market attachment requires approved=True."""
    __tablename__ = "team_alias"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("team.id"), nullable=False)
    alias = Column(String(80), nullable=False)
    source = Column(String(24), nullable=False)   # kalshi|espn|apifootball
    approved = Column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("source", "alias"),)


class Fixture(LiveBase):
    __tablename__ = "fixture"
    id = Column(Integer, primary_key=True)
    competition_slug = Column(String(32),
                              ForeignKey("competition.slug"),
                              nullable=False)
    provider_fixture_id = Column(String(32))
    espn_event_id = Column(String(16))
    home_team_id = Column(Integer, ForeignKey("team.id"))
    away_team_id = Column(Integer, ForeignKey("team.id"))
    original_kickoff_utc = Column(DateTime(timezone=True))
    current_kickoff_utc = Column(DateTime(timezone=True))
    venue = Column(String(96))
    status = Column(String(16))
    home_goals = Column(Integer)          # final score once status=post
    away_goals = Column(Integer)
    round = Column(String(32))
    observed_at = Column(DateTime(timezone=True))
    provider_updated_at = Column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("competition_slug", "espn_event_id"),
    )


class FixtureChange(LiveBase):
    """Reschedules create history, never silent overwrite."""
    __tablename__ = "fixture_change"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixture.id"), nullable=False)
    field = Column(String(32), nullable=False)
    old_value = Column(String(96))
    new_value = Column(String(96))
    observed_at = Column(DateTime(timezone=True), nullable=False)


class SourceObservation(LiveBase):
    """Raw provider responses, content-hashed — the bottom of every
    evidence chain."""
    __tablename__ = "source_observation"
    id = Column(Integer, primary_key=True)
    source = Column(String(24), nullable=False)    # espn|kalshi|apifootball
    endpoint = Column(String(160), nullable=False)
    params_json = Column(Text)
    content_hash = Column(String(64), nullable=False)
    payload_json = Column(Text)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    provider_timestamp = Column(DateTime(timezone=True))


class ModelVersion(LiveBase):
    __tablename__ = "model_version"
    id = Column(Integer, primary_key=True)
    name = Column(String(48), unique=True, nullable=False)  # mls-2026-v0
    description = Column(Text)
    approved_for_shadow = Column(Boolean, default=False, nullable=False)
    approved_for_real_money = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True))


class Player(LiveBase):
    """Player identity (V8.1 evaluation Phase 5). Keyed by provider id;
    team membership + availability live in the snapshot tables so a
    provider correction never overwrites what was true at T-10."""
    __tablename__ = "player"
    id = Column(Integer, primary_key=True)
    competition_slug = Column(String(32), ForeignKey("competition.slug"))
    espn_id = Column(String(16), unique=True)
    name = Column(String(96))
    position = Column(String(8))


class LineupSnapshot(LiveBase):
    """As-of team-selection state for a fixture, with full provenance.
    A T-10 run references the EXACT snapshot it saw — missing/unconfirmed
    lineups are recorded as such, never silently treated as confidence."""
    __tablename__ = "lineup_snapshot"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixture.id"), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    observed_at = Column(DateTime(timezone=True))
    provider = Column(String(24))
    parser_version = Column(String(16))
    source_observation_id = Column(
        Integer, ForeignKey("source_observation.id"))
    status = Column(String(16))              # confirmed | partial | pending
    home_confirmed = Column(Boolean)
    away_confirmed = Column(Boolean)
    home_formation = Column(String(16))
    away_formation = Column(String(16))
    home_gk_player_id = Column(Integer, ForeignKey("player.id"))
    away_gk_player_id = Column(Integer, ForeignKey("player.id"))


class LineupEntry(LiveBase):
    """One player's selection state within a lineup snapshot."""
    __tablename__ = "lineup_entry"
    id = Column(Integer, primary_key=True)
    lineup_snapshot_id = Column(
        Integer, ForeignKey("lineup_snapshot.id"), nullable=False)
    side = Column(String(8), nullable=False)   # home | away
    player_id = Column(Integer, ForeignKey("player.id"))
    starter = Column(Boolean)
    is_goalkeeper = Column(Boolean)
    position = Column(String(8))
    jersey = Column(String(8))


class ModelInputArtifact(LiveBase):
    """The exact, retrievable input DOCUMENT a run simulated from
    (V8.1 evaluation Phase 2 / qualification #1). input_snapshot_hash
    proves integrity; this stores the BYTES so another machine can
    replay the run and get the same probabilities. Deduped by
    content_hash — identical inputs share one artifact."""
    __tablename__ = "model_input_artifact"
    id = Column(Integer, primary_key=True)
    schema_version = Column(String(24), nullable=False)
    content_hash = Column(String(64), unique=True, nullable=False)
    size_bytes = Column(Integer)
    document_json = Column(Text, nullable=False)   # canonical serialization
    created_at = Column(DateTime(timezone=True))


class PredictionRun(LiveBase):
    __tablename__ = "prediction_run"
    id = Column(String(36), primary_key=True, default=_uuid)
    fixture_id = Column(Integer, ForeignKey("fixture.id"), nullable=False)
    run_type = Column(String(16), nullable=False)  # scheduled|t60|t10|live
    scheduled_for = Column(DateTime(timezone=True))
    captured_at = Column(DateTime(timezone=True))
    seconds_before_kickoff = Column(Integer)
    status = Column(String(12), nullable=False, default="writing")
    canonical = Column(Boolean, nullable=False, default=False)
    model_version_id = Column(Integer, ForeignKey("model_version.id"))
    git_revision = Column(String(40))
    simulation_seed = Column(Integer)
    simulation_count = Column(Integer)
    input_snapshot_hash = Column(String(64))
    model_input_artifact_id = Column(
        Integer, ForeignKey("model_input_artifact.id"))
    team_snapshot_id = Column(Integer)
    player_snapshot_id = Column(Integer)
    availability_snapshot_id = Column(Integer)
    lineup_snapshot_id = Column(Integer)
    market_snapshot_id = Column(Integer)
    created_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    failure_reason = Column(Text)
    # display extras (xg, scorelines, props, basis) frozen WITH the run —
    # recomputing later against refreshed ratings would silently diverge
    # from the stored contracts
    payload_json = Column(Text)
    # immutable approval-decision record: whether the model version was
    # approved for shadow AT CAPTURE TIME (V8.1 eval — flipping the
    # ModelVersion flag later must not retroactively re-authorize an old
    # run). Frozen True here because the F3 gate refuses to run otherwise.
    model_approved_at_run = Column(Boolean)
    # input-quality states frozen with the run (V8.1 eval Phase 5):
    # TEAM_DATA_FRESH / PLAYER_DATA_FRESH / AVAILABILITY_COMPLETE /
    # LINEUP_CONFIRMED / GOALKEEPER_CONFIRMED. Missing data is recorded
    # as false, never absorbed into the model as confidence.
    input_quality_json = Column(Text)
    __table_args__ = (
        # ONE canonical complete T-10 per fixture — the same partial
        # unique invariant on SQLite (tests) and PostgreSQL (production).
        # Explicit per-dialect WHERE text: building this from detached
        # typeless Column() objects rendered `canonical IS 1`, which
        # SQLite accepts and PostgreSQL rejects — the first live-plane
        # migration died on it (Jul 23). A compilation test now pins
        # both dialects' DDL.
        Index("uq_fixture_canonical_t10", "fixture_id",
              unique=True,
              postgresql_where=text(
                  "run_type = 't10' AND canonical AND "
                  "status = 'complete'"),
              sqlite_where=text(
                  "run_type = 't10' AND canonical = 1 AND "
                  "status = 'complete'")),
    )


class PredictionContract(LiveBase):
    __tablename__ = "prediction_contract"
    id = Column(Integer, primary_key=True)
    prediction_run_id = Column(String(36),
                               ForeignKey("prediction_run.id"),
                               nullable=False)
    market_contract_id = Column(Integer,
                                ForeignKey("market_contract.id"))
    outcome_key = Column(String(32), nullable=False)
    raw_probability = Column(Float, nullable=False)
    anchored_probability = Column(Float)
    market_quote_id = Column(Integer, ForeignKey("market_quote.id"))
    __table_args__ = (
        UniqueConstraint("prediction_run_id", "market_contract_id"),
        # SQL NULLs are pairwise-distinct, so the constraint above never
        # fired for unmapped contracts (V8 evaluation): the outcome key
        # itself must be unique per run
        UniqueConstraint("prediction_run_id", "outcome_key"),
    )


class MarketEvent(LiveBase):
    __tablename__ = "market_event"
    id = Column(Integer, primary_key=True)
    competition_slug = Column(String(32),
                              ForeignKey("competition.slug"))
    kalshi_event_ticker = Column(String(64), unique=True, nullable=False)
    series = Column(String(24))                     # KXMLSGAME | KXMLSCUP
    title = Column(String(120))
    fixture_id = Column(Integer, ForeignKey("fixture.id"))
    settlement_scope = Column(String(24))           # regular_time | ...
    mapped_via = Column(String(24))                 # alias | manual
    mapping_approved = Column(Boolean, default=False, nullable=False)


class MarketContract(LiveBase):
    __tablename__ = "market_contract"
    id = Column(Integer, primary_key=True)
    market_event_id = Column(Integer, ForeignKey("market_event.id"),
                             nullable=False)
    ticker = Column(String(80), unique=True, nullable=False)
    side_label = Column(String(64))
    outcome_key = Column(String(32))                # home_win|draw|away_win


class MarketSnapshot(LiveBase):
    """The atomic evidence header a T-10 lock points at (V8 evaluation
    F1): one row per capture attempt, with expected-vs-actual coverage
    counts and a status gate. A run may only become canonical against a
    snapshot whose status is 'complete' — a zero-quote or partial
    capture stays 'failed' and the lock visibly does not happen."""
    __tablename__ = "market_snapshot"
    id = Column(Integer, primary_key=True)
    fixture_id = Column(Integer, ForeignKey("fixture.id"), nullable=False)
    captured_at = Column(DateTime(timezone=True), nullable=False)
    # `status` is CAPTURE-completeness only (all expected records
    # observed or explicitly recorded absent). Tradeability is a
    # SEPARATE concept — `execution_ready` — because a complete
    # capture can legitimately contain no-bid contracts (V8.1 eval
    # qualification #2). The lock predicate itself is versioned so
    # "full book" cannot change meaning silently (qualification #3).
    status = Column(String(12), nullable=False, default="writing")
    policy_version = Column(String(24))
    provider_schema_version = Column(String(32))
    events_expected = Column(Integer)
    events_captured = Column(Integer)
    contracts_expected = Column(Integer)
    quotes_written = Column(Integer)
    quotes_with_prices = Column(Integer)
    quotes_without_prices = Column(Integer)
    depth_rows_written = Column(Integer)
    oldest_quote_age_seconds = Column(Integer)
    required_families_complete = Column(Boolean)
    execution_ready = Column(Boolean)
    failure_reason = Column(Text)


class MarketQuote(LiveBase):
    """Full-book quote in integer CENTS (fixed point, never binary
    float). YES ask derives from NO bid (1 - no_bid) and vice versa —
    both stored as captured."""
    __tablename__ = "market_quote"
    id = Column(Integer, primary_key=True)
    market_contract_id = Column(Integer,
                                ForeignKey("market_contract.id"),
                                nullable=False)
    market_snapshot_id = Column(Integer,
                                ForeignKey("market_snapshot.id"))
    captured_at = Column(DateTime(timezone=True), nullable=False)
    provider_timestamp = Column(DateTime(timezone=True))
    yes_bid_c = Column(Integer)
    yes_bid_size = Column(Integer)
    yes_ask_c = Column(Integer)
    yes_ask_size = Column(Integer)
    no_bid_c = Column(Integer)
    no_bid_size = Column(Integer)
    no_ask_c = Column(Integer)
    no_ask_size = Column(Integer)
    last_trade_c = Column(Integer)
    last_trade_at = Column(DateTime(timezone=True))
    volume = Column(Integer)
    open_interest = Column(Integer)
    status = Column(String(16))
    rules_hash = Column(String(64))
    fee_schedule_version = Column(String(16))
    source_observation_id = Column(Integer,
                                   ForeignKey("source_observation.id"))


class MarketDepthLevel(LiveBase):
    __tablename__ = "market_depth_level"
    id = Column(Integer, primary_key=True)
    market_quote_id = Column(Integer, ForeignKey("market_quote.id"),
                             nullable=False)
    side = Column(String(8), nullable=False)        # yes | no
    price_c = Column(Integer, nullable=False)
    size = Column(Integer, nullable=False)
