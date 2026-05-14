"""Widen fundamental growth columns: Numeric(8,4) -> Numeric(12,4)

Some yfinance symbols (e.g. micro-caps emerging from near-zero revenue)
return growth ratios above 10,000 which overflows Numeric(8,4).
Numeric(12,4) holds up to ~99 million which covers any real data.

Revision ID: g8h9i0j1k2l3
Revises: f7a8b9c0d1e2
Create Date: 2026-05-12 10:00:00.000000
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = 'g8h9i0j1k2l3'
down_revision: str | Sequence[str] | None = 'f7a8b9c0d1e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLS = ['revenue_growth', 'net_income_growth', 'earnings_annual_growth', 'net_margin', 'roe']


def upgrade() -> None:
    for col in _COLS:
        op.alter_column(
            'screener_scores', col,
            existing_type=sa.Numeric(precision=8, scale=4),
            type_=sa.Numeric(precision=12, scale=4),
            existing_nullable=True,
            postgresql_using=f'{col}::numeric(12,4)',
        )


def downgrade() -> None:
    for col in reversed(_COLS):
        op.alter_column(
            'screener_scores', col,
            existing_type=sa.Numeric(precision=12, scale=4),
            type_=sa.Numeric(precision=8, scale=4),
            existing_nullable=True,
            postgresql_using=f'{col}::numeric(8,4)',
        )
