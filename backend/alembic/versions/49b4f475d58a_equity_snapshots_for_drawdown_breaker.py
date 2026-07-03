"""equity snapshots for drawdown breaker

Revision ID: 49b4f475d58a
Revises: j1k2l3m4n5o6
Create Date: 2026-07-02 19:54:47.051337

Note: autogenerate also detected pre-existing drift (numeric precision on
screener_scores/trailing_actions, renamed indexes). That drift predates this
change and is intentionally NOT included here — this migration only adds the
equity_snapshots table.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = '49b4f475d58a'
down_revision: str | Sequence[str] | None = 'j1k2l3m4n5o6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('equity_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('account_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('snapshot_date', sa.DateTime(), nullable=False),
    sa.Column('cash', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('market_value', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('total_equity', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'currency', 'snapshot_date', name='uq_equity_snapshot_account_ccy_date')
    )
    op.create_index(op.f('ix_equity_snapshots_account_id'), 'equity_snapshots', ['account_id'], unique=False)
    op.create_index(op.f('ix_equity_snapshots_snapshot_date'), 'equity_snapshots', ['snapshot_date'], unique=False)
    op.create_index(op.f('ix_equity_snapshots_user_id'), 'equity_snapshots', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_equity_snapshots_user_id'), table_name='equity_snapshots')
    op.drop_index(op.f('ix_equity_snapshots_snapshot_date'), table_name='equity_snapshots')
    op.drop_index(op.f('ix_equity_snapshots_account_id'), table_name='equity_snapshots')
    op.drop_table('equity_snapshots')
