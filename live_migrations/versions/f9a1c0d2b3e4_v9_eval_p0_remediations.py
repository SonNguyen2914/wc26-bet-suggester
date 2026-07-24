"""V9 eval P0 remediations: approval decisions, corpus exports,
provenance FKs, exact market precision, freshness basis.

Covers V9 independent-evaluation P0 findings:
  F1/F10 model_approval_decision (immutable CI-based approval record)
         + prediction_run.model_approval_decision_id
  F5     real foreign keys for prediction_run.lineup_snapshot_id and
         .market_snapshot_id (PostgreSQL-native; the SQLite test plane
         builds from metadata via create_all, and rebuilding
         prediction_run risks the partial unique index, so the FK add is
         PG-only)
  F7     exact fixed-point columns on market_quote + market_depth_level
  F9     game-specific freshness columns on market_snapshot
  F3     corpus_export (immutable published corpus bytes)

Additive columns and new tables are plain CREATE/ADD on both dialects;
only the two constraints on the existing prediction_run columns are
guarded to PostgreSQL.

Revision ID: f9a1c0d2b3e4
Revises: 755ded7a27ff
Create Date: 2026-07-23 13:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f9a1c0d2b3e4'
down_revision = '755ded7a27ff'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- F1/F10: immutable model-approval decision record ---------------
    op.create_table(
        'model_approval_decision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('model_version_id', sa.Integer(), nullable=False),
        sa.Column('model_version_name', sa.String(length=48), nullable=True),
        sa.Column('eval_version', sa.String(length=24), nullable=True),
        sa.Column('policy_version', sa.String(length=24), nullable=True),
        sa.Column('corpus_version', sa.String(length=48), nullable=True),
        sa.Column('approved_mode', sa.String(length=16), nullable=False),
        sa.Column('approved', sa.Boolean(), nullable=False),
        sa.Column('n_scored', sa.Integer(), nullable=True),
        sa.Column('metrics_json', sa.Text(), nullable=True),
        sa.Column('edge_json', sa.Text(), nullable=True),
        sa.Column('limitations_json', sa.Text(), nullable=True),
        sa.Column('report_json', sa.Text(), nullable=True),
        sa.Column('approved_by', sa.String(length=32), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['model_version_id'], ['model_version.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('content_hash',
                            name='uq_model_approval_content_hash'),
    )

    # --- F3: immutable published corpus bytes ---------------------------
    op.create_table(
        'corpus_export',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=48), nullable=False),
        sa.Column('schema_version', sa.String(length=24), nullable=True),
        sa.Column('manifest_hash', sa.String(length=64), nullable=False),
        sa.Column('manifest_json', sa.Text(), nullable=False),
        sa.Column('bundle_json', sa.Text(), nullable=False),
        sa.Column('backend_revision', sa.String(length=40), nullable=True),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version', name='uq_corpus_export_version'),
    )

    # --- F1: prediction_run -> approval decision ------------------------
    op.add_column('prediction_run',
                  sa.Column('model_approval_decision_id', sa.Integer(),
                            nullable=True))

    # --- F7: exact provider precision on quotes + depth -----------------
    for col in ('yes_bid_dollars', 'yes_ask_dollars',
                'no_bid_dollars', 'no_ask_dollars'):
        op.add_column('market_quote',
                      sa.Column(col, sa.String(length=16), nullable=True))
    op.add_column('market_quote',
                  sa.Column('sizes_fp_json', sa.Text(), nullable=True))
    op.add_column('market_quote',
                  sa.Column('provider_precision', sa.String(length=24),
                            nullable=True))
    op.add_column('market_depth_level',
                  sa.Column('price_dollars', sa.String(length=16),
                            nullable=True))
    op.add_column('market_depth_level',
                  sa.Column('size_fp', sa.String(length=24), nullable=True))

    # --- F9: game-specific freshness on the lock snapshot ---------------
    op.add_column('market_snapshot',
                  sa.Column('game_oldest_quote_age_seconds', sa.Integer(),
                            nullable=True))
    op.add_column('market_snapshot',
                  sa.Column('freshness_basis', sa.String(length=16),
                            nullable=True))

    # --- F5/F1: real provenance foreign keys ----------------------------
    # PostgreSQL-native ADD CONSTRAINT (no table rewrite). SQLite cannot
    # add a FK to an existing column without recreating the table, and
    # recreating prediction_run risks the dialect-specific partial unique
    # index (uq_fixture_canonical_t10) that a prior migration already died
    # on — so on SQLite these stay logical-only (the create_all test plane
    # picks them up from the ORM model). Production is PostgreSQL, which is
    # exactly where the evaluator's F5 integrity gap lives.
    if op.get_bind().dialect.name == 'postgresql':
        op.create_foreign_key(
            'fk_prediction_run_approval_decision', 'prediction_run',
            'model_approval_decision', ['model_approval_decision_id'], ['id'],
            ondelete='RESTRICT')
        op.create_foreign_key(
            'fk_prediction_run_lineup_snapshot', 'prediction_run',
            'lineup_snapshot', ['lineup_snapshot_id'], ['id'],
            ondelete='RESTRICT')
        op.create_foreign_key(
            'fk_prediction_run_market_snapshot', 'prediction_run',
            'market_snapshot', ['market_snapshot_id'], ['id'],
            ondelete='RESTRICT')


def downgrade() -> None:
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_constraint('fk_prediction_run_market_snapshot',
                           'prediction_run', type_='foreignkey')
        op.drop_constraint('fk_prediction_run_lineup_snapshot',
                           'prediction_run', type_='foreignkey')
        op.drop_constraint('fk_prediction_run_approval_decision',
                           'prediction_run', type_='foreignkey')
    op.drop_column('market_snapshot', 'freshness_basis')
    op.drop_column('market_snapshot', 'game_oldest_quote_age_seconds')
    op.drop_column('market_depth_level', 'size_fp')
    op.drop_column('market_depth_level', 'price_dollars')
    op.drop_column('market_quote', 'provider_precision')
    op.drop_column('market_quote', 'sizes_fp_json')
    for col in ('no_ask_dollars', 'no_bid_dollars',
                'yes_ask_dollars', 'yes_bid_dollars'):
        op.drop_column('market_quote', col)
    op.drop_column('prediction_run', 'model_approval_decision_id')
    op.drop_table('corpus_export')
    op.drop_table('model_approval_decision')
