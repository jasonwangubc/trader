"""earnings_annual_growth column for acceleration detection

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-05-11 10:00:00.000000

"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = 'f7a8b9c0d1e2'
down_revision: str | Sequence[str] | None = 'e6f7a8b9c0d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('screener_scores',
        sa.Column('earnings_annual_growth', sa.Numeric(precision=8, scale=4), nullable=True))


def downgrade() -> None:
    op.drop_column('screener_scores', 'earnings_annual_growth')
