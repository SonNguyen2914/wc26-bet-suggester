"""V9.1 execution-fidelity + governance hotfix.

Additive columns + one table for the fourth independent evaluation's P0s:
  F2/F3  exact provider-precision economics on the paper ledger
         (paper_signal.ask_dollars/fee_dollars;
          paper_fill.filled_contracts_fp/avg_fill_price_dollars/
          fee_dollars/cost_dollars/payout_dollars/pnl_dollars)
  F4     model_approval_decision.decision_document (the canonical bytes the
         content hash covers, so the audit can recompute/verify it)
  F10    registry_discovery (durable market-discovery completeness record)

All additive — no data migration, no constraint on existing rows.

Revision ID: a2b3c4d5e6f7
Revises: f9a1c0d2b3e4
Create Date: 2026-07-24 03:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'a2b3c4d5e6f7'
down_revision = 'f9a1c0d2b3e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # F4: the canonical decision document the content hash covers
    op.add_column('model_approval_decision',
                  sa.Column('decision_document', sa.Text(), nullable=True))

    # F2/F3: exact economics on paper signals
    op.add_column('paper_signal',
                  sa.Column('ask_dollars', sa.String(length=16),
                            nullable=True))
    op.add_column('paper_signal',
                  sa.Column('fee_dollars', sa.String(length=16),
                            nullable=True))

    # F2/F3: exact economics on paper fills
    for col, length in (('filled_contracts_fp', 24),
                        ('avg_fill_price_dollars', 16),
                        ('fee_dollars', 16), ('cost_dollars', 24),
                        ('payout_dollars', 24), ('pnl_dollars', 24)):
        op.add_column('paper_fill',
                      sa.Column(col, sa.String(length=length), nullable=True))

    # F10: durable registry-discovery completeness record
    op.create_table(
        'registry_discovery',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('competition_slug', sa.String(length=32), nullable=True),
        sa.Column('provider', sa.String(length=24), nullable=True),
        sa.Column('complete', sa.Boolean(), nullable=False),
        sa.Column('truncated_series_json', sa.Text(), nullable=True),
        sa.Column('events_seen', sa.Integer(), nullable=True),
        sa.Column('newly_mapped', sa.Integer(), nullable=True),
        sa.Column('unmapped', sa.Integer(), nullable=True),
        sa.Column('contracts_filled', sa.Integer(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('registry_discovery')
    for col in ('pnl_dollars', 'payout_dollars', 'cost_dollars',
                'fee_dollars', 'avg_fill_price_dollars',
                'filled_contracts_fp'):
        op.drop_column('paper_fill', col)
    op.drop_column('paper_signal', 'fee_dollars')
    op.drop_column('paper_signal', 'ask_dollars')
    op.drop_column('model_approval_decision', 'decision_document')
