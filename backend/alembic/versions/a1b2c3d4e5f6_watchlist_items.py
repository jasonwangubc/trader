"""watchlist items

Revision ID: a1b2c3d4e5f6
Revises: 9c7b64c64f48
Create Date: 2026-07-03 00:00:00.000000

Stage-2 pivot watchlist: persisted bridge between Tier S/A screener picks and
armed tickets. See app/db/models.py WatchlistItem and
app/services/watchlist_service.py.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision: str | Sequence[str] | None = '9c7b64c64f48'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('watchlist_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('pivot_price', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('pattern_type', sa.String(length=24), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_notified_status', sa.String(length=16), nullable=True),
    sa.Column('ticket_id', sa.UUID(), nullable=True),
    sa.Column('notes', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_watchlist_items_user_id'), 'watchlist_items', ['user_id'], unique=False)
    op.create_index(op.f('ix_watchlist_items_symbol'), 'watchlist_items', ['symbol'], unique=False)
    op.create_index(op.f('ix_watchlist_items_status'), 'watchlist_items', ['status'], unique=False)
    op.create_index(op.f('ix_watchlist_items_ticket_id'), 'watchlist_items', ['ticket_id'], unique=False)
    op.create_index(
        'ix_watchlist_items_user_symbol_status', 'watchlist_items',
        ['user_id', 'symbol', 'status'], unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_watchlist_items_user_symbol_status', table_name='watchlist_items')
    op.drop_index(op.f('ix_watchlist_items_ticket_id'), table_name='watchlist_items')
    op.drop_index(op.f('ix_watchlist_items_status'), table_name='watchlist_items')
    op.drop_index(op.f('ix_watchlist_items_symbol'), table_name='watchlist_items')
    op.drop_index(op.f('ix_watchlist_items_user_id'), table_name='watchlist_items')
    op.drop_table('watchlist_items')
