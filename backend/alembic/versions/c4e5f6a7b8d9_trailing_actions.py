"""trailing_actions table

Revision ID: c4e5f6a7b8d9
Revises: b3c1d4e2f5a7
Create Date: 2026-05-10 12:00:00.000000

"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = 'c4e5f6a7b8d9'
down_revision: str | Sequence[str] | None = 'b3c1d4e2f5a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'trailing_actions',
        sa.Column('id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.String(128), nullable=False, server_default='user_default'),
        sa.Column('ticket_id', sa.UUID(as_uuid=True), nullable=False),
        sa.Column('action_type', sa.String(32), nullable=False),
        sa.Column('milestone', sa.String(32), nullable=False),
        sa.Column('old_stop', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('new_stop', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('sell_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('sell_shares', sa.Integer(), nullable=True),
        sa.Column('leg_label', sa.String(32), nullable=True),
        sa.Column('open_r', sa.Numeric(precision=6, scale=2), nullable=False, server_default='0'),
        sa.Column('triggered_price', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('triggered_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('broker_order_id', sa.String(64), nullable=True),
        sa.Column('execution_price', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('error_msg', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_trailing_actions_ticket_id', 'trailing_actions', ['ticket_id'])
    op.create_index('ix_trailing_actions_user_id', 'trailing_actions', ['user_id'])
    op.create_index('ix_trailing_actions_status', 'trailing_actions', ['status'])


def downgrade() -> None:
    op.drop_index('ix_trailing_actions_status', 'trailing_actions')
    op.drop_index('ix_trailing_actions_user_id', 'trailing_actions')
    op.drop_index('ix_trailing_actions_ticket_id', 'trailing_actions')
    op.drop_table('trailing_actions')
