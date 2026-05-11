"""volume → BIGINT (penny stocks can do >2.1B shares/day)

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-05-10 16:00:00.000000

"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = 'e6f7a8b9c0d1'
down_revision: str | Sequence[str] | None = 'd5e6f7a8b9c0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('daily_bars', 'volume',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False,
                    postgresql_using='volume::bigint')
    op.alter_column('earnings_dates', 'avg_volume',
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=True,
                    postgresql_using='avg_volume::bigint')


def downgrade() -> None:
    op.alter_column('earnings_dates', 'avg_volume',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=True,
                    postgresql_using='avg_volume::int')
    op.alter_column('daily_bars', 'volume',
                    existing_type=sa.BigInteger(),
                    type_=sa.Integer(),
                    existing_nullable=False,
                    postgresql_using='volume::int')
