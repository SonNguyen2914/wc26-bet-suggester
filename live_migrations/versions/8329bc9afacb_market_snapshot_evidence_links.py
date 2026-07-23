"""market snapshot + evidence links (V8 evaluation F1/F2 + constraint gap)

- market_snapshot: the completeness-gated evidence header a canonical
  T-10 lock must point at.
- market_quote.market_snapshot_id: quotes belonging to a lock snapshot.
- UNIQUE(prediction_run_id, outcome_key) on prediction_contract: the
  existing (run, market_contract_id) constraint never fired for NULL
  contract ids (SQL NULLs are pairwise-distinct). Existing duplicates
  are removed keeping the lowest id before the constraint lands.

Batch mode throughout: SQLite (the test dialect) cannot ADD CONSTRAINT
in place; batch recreates the table there and degrades to plain ALTER
on PostgreSQL.

Revision ID: 8329bc9afacb
Revises: 9a5690b3f282
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa


revision = '8329bc9afacb'
down_revision = '9a5690b3f282'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'market_snapshot',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('fixture_id', sa.Integer(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True),
                  nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('provider_schema_version', sa.String(length=32),
                  nullable=True),
        sa.Column('events_expected', sa.Integer(), nullable=True),
        sa.Column('events_captured', sa.Integer(), nullable=True),
        sa.Column('contracts_expected', sa.Integer(), nullable=True),
        sa.Column('quotes_written', sa.Integer(), nullable=True),
        sa.Column('depth_rows_written', sa.Integer(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['fixture_id'], ['fixture.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('market_quote') as batch:
        batch.add_column(
            sa.Column('market_snapshot_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_market_quote_snapshot',
                                 'market_snapshot',
                                 ['market_snapshot_id'], ['id'])
    # duplicate outcome rows (possible pre-constraint) go first
    op.execute(sa.text(
        "DELETE FROM prediction_contract WHERE id NOT IN ("
        "SELECT MIN(id) FROM prediction_contract "
        "GROUP BY prediction_run_id, outcome_key)"))
    with op.batch_alter_table('prediction_contract') as batch:
        batch.create_unique_constraint(
            'uq_run_outcome_key', ['prediction_run_id', 'outcome_key'])


def downgrade() -> None:
    with op.batch_alter_table('prediction_contract') as batch:
        batch.drop_constraint('uq_run_outcome_key', type_='unique')
    with op.batch_alter_table('market_quote') as batch:
        batch.drop_constraint('fk_market_quote_snapshot',
                              type_='foreignkey')
        batch.drop_column('market_snapshot_id')
    op.drop_table('market_snapshot')
