"""mls official per-match team + player stats (Sportec/StatsPerform)

Two new tables holding the richer external MLS stats — real provider xG
and shot volume per team, plus per-match player rows (xG, minutes, GK
flag) — attached to our fixtures by (kickoff date, resolved club ids).
Purely additive: new tables only, plain DDL valid on SQLite and
PostgreSQL. No model output changes until a feature built on this data
is measured to beat the current model.

Revision ID: b7c8d9e0f1a2
Revises: a2b3c4d5e6f7
Create Date: 2026-07-24
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7c8d9e0f1a2'
down_revision = 'a2b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'mls_team_match_stat',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('side', sa.String(length=8), nullable=False),
        sa.Column('sportec_match_id', sa.String(length=32), nullable=True),
        sa.Column('sportec_club_id', sa.String(length=32), nullable=True),
        sa.Column('goals', sa.Integer(), nullable=True),
        sa.Column('goals_conceded', sa.Integer(), nullable=True),
        sa.Column('xg', sa.Float(), nullable=True),
        sa.Column('xg_against', sa.Float(), nullable=True),
        sa.Column('shots_total', sa.Integer(), nullable=True),
        sa.Column('shots_inside_box', sa.Integer(), nullable=True),
        sa.Column('shots_outside_box', sa.Integer(), nullable=True),
        sa.Column('shots_on_target', sa.Integer(), nullable=True),
        sa.Column('corners', sa.Integer(), nullable=True),
        sa.Column('passes_successful', sa.Integer(), nullable=True),
        sa.Column('passes_total', sa.Integer(), nullable=True),
        sa.Column('source_observation_id', sa.Integer(), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixture.id'], ),
        sa.ForeignKeyConstraint(['team_id'], ['team.id'], ),
        sa.ForeignKeyConstraint(['source_observation_id'],
                                ['source_observation.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fixture_id', 'side'),
    )
    op.create_table(
        'mls_player_match_stat',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('side', sa.String(length=8), nullable=True),
        sa.Column('sportec_match_id', sa.String(length=32), nullable=True),
        sa.Column('sportec_club_id', sa.String(length=32), nullable=True),
        sa.Column('sportec_player_id', sa.String(length=32), nullable=True),
        sa.Column('player_name', sa.String(length=96), nullable=True),
        sa.Column('is_goalkeeper', sa.Boolean(), nullable=True),
        sa.Column('minutes', sa.Float(), nullable=True),
        sa.Column('goals', sa.Integer(), nullable=True),
        sa.Column('assists', sa.Integer(), nullable=True),
        sa.Column('xg', sa.Float(), nullable=True),
        sa.Column('shots_total', sa.Integer(), nullable=True),
        sa.Column('shots_on_target', sa.Integer(), nullable=True),
        sa.Column('shots_faced', sa.Integer(), nullable=True),
        sa.Column('source_observation_id', sa.Integer(), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixture.id'], ),
        sa.ForeignKeyConstraint(['team_id'], ['team.id'], ),
        sa.ForeignKeyConstraint(['source_observation_id'],
                                ['source_observation.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fixture_id', 'sportec_player_id'),
    )


def downgrade() -> None:
    op.drop_table('mls_player_match_stat')
    op.drop_table('mls_team_match_stat')
