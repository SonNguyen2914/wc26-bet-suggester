"""retrievable model input artifact (V8.1 eval Phase 2 / qual #1)

Stores the exact input DOCUMENT each run simulated from, so a run can
be replayed independently. Batch mode for the prediction_run FK
(SQLite cannot add a foreign key in place; PostgreSQL does a plain
ALTER).

Revision ID: c21ba2ee8df4
Revises: 9673668959a8
Create Date: 2026-07-23
"""
from alembic import op
import sqlalchemy as sa


revision = 'c21ba2ee8df4'
down_revision = '9673668959a8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'model_input_artifact',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('schema_version', sa.String(length=24), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('document_json', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('content_hash'),
    )
    with op.batch_alter_table('prediction_run') as batch:
        batch.add_column(
            sa.Column('model_input_artifact_id', sa.Integer(),
                      nullable=True))
        batch.create_foreign_key('fk_run_input_artifact',
                                 'model_input_artifact',
                                 ['model_input_artifact_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('prediction_run') as batch:
        batch.drop_constraint('fk_run_input_artifact', type_='foreignkey')
        batch.drop_column('model_input_artifact_id')
    op.drop_table('model_input_artifact')
