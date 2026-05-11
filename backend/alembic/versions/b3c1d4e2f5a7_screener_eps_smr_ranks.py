"""screener eps/smr ranks + roe column

Revision ID: b3c1d4e2f5a7
Revises: 051877be6d50
Create Date: 2026-05-08 16:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b3c1d4e2f5a7'
down_revision: str | Sequence[str] | None = '051877be6d50'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('screener_scores', sa.Column('roe', sa.Numeric(precision=8, scale=4), nullable=True))
    op.add_column('screener_scores', sa.Column('eps_rank', sa.Integer(), nullable=True))
    op.add_column('screener_scores', sa.Column('smr_rank', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('screener_scores', 'smr_rank')
    op.drop_column('screener_scores', 'eps_rank')
    op.drop_column('screener_scores', 'roe')
