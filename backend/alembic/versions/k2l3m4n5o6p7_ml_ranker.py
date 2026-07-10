"""ML setup ranker

Revision ID: k2l3m4n5o6p7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-08 00:00:00.000000

Learned setup ranker: point-in-time feature snapshots on scan candidates,
calibrated ml_score on screener rows, and the ml_models registry. See
app/services/ml_features.py, ml_labels.py, ml_ranker.py.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'k2l3m4n5o6p7'
down_revision: str | Sequence[str] | None = 'a1b2c3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'backtest_signal_candidates',
        sa.Column('features', postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        'screener_scores',
        sa.Column('ml_score', sa.Numeric(precision=5, scale=4), nullable=True),
    )
    op.add_column(
        'screener_scores',
        sa.Column('ml_details', postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        'ml_models',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('scan_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.Column('label_kind', sa.String(length=32), nullable=True),
        sa.Column('feature_version', sa.Integer(), nullable=False),
        sa.Column('params', postgresql.JSONB(), nullable=False),
        sa.Column('feature_names', postgresql.JSONB(), nullable=False),
        sa.Column('metrics', postgresql.JSONB(), nullable=False),
        sa.Column('feature_importances', postgresql.JSONB(), nullable=False),
        sa.Column('n_train', sa.Integer(), nullable=False),
        sa.Column('n_valid', sa.Integer(), nullable=False),
        sa.Column('train_end_date', sa.DateTime(), nullable=True),
        sa.Column('valid_start_date', sa.DateTime(), nullable=True),
        sa.Column('artifact_path', sa.String(length=400), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['scan_id'], ['backtest_signal_scans.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ml_models_is_active'), 'ml_models', ['is_active'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ml_models_is_active'), table_name='ml_models')
    op.drop_table('ml_models')
    op.drop_column('screener_scores', 'ml_details')
    op.drop_column('screener_scores', 'ml_score')
    op.drop_column('backtest_signal_candidates', 'features')
