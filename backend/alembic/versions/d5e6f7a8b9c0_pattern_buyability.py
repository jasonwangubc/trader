"""pattern + buyability fields on screener_scores

Revision ID: d5e6f7a8b9c0
Revises: c4e5f6a7b8d9
Create Date: 2026-05-10 14:00:00.000000

"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = 'd5e6f7a8b9c0'
down_revision: str | Sequence[str] | None = 'c4e5f6a7b8d9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('screener_scores', sa.Column('pattern_type', sa.String(24), nullable=True))
    op.add_column('screener_scores', sa.Column('pattern_quality', sa.Numeric(precision=4, scale=3), nullable=True))
    op.add_column('screener_scores', sa.Column('buyability', sa.String(16), nullable=True))
    op.add_column('screener_scores', sa.Column('pivot_price', sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('screener_scores', sa.Column('base_low', sa.Numeric(precision=12, scale=4), nullable=True))
    op.add_column('screener_scores', sa.Column('base_length_days', sa.Integer(), nullable=True))
    op.add_column('screener_scores', sa.Column('base_depth_pct', sa.Numeric(precision=6, scale=2), nullable=True))
    op.add_column('screener_scores', sa.Column('extension_pct', sa.Numeric(precision=6, scale=2), nullable=True))
    op.create_index('ix_screener_scores_buyability', 'screener_scores', ['buyability'])


def downgrade() -> None:
    op.drop_index('ix_screener_scores_buyability', 'screener_scores')
    op.drop_column('screener_scores', 'extension_pct')
    op.drop_column('screener_scores', 'base_depth_pct')
    op.drop_column('screener_scores', 'base_length_days')
    op.drop_column('screener_scores', 'base_low')
    op.drop_column('screener_scores', 'pivot_price')
    op.drop_column('screener_scores', 'buyability')
    op.drop_column('screener_scores', 'pattern_quality')
    op.drop_column('screener_scores', 'pattern_type')
