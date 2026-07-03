"""charter versions and account cash flows

Revision ID: 9c7b64c64f48
Revises: 49b4f475d58a
Create Date: 2026-07-02 20:08:28.790561

Pre-existing autogenerate drift (numeric precision, renamed indexes) is
intentionally excluded — this migration only adds the two charter tables.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '9c7b64c64f48'
down_revision: str | Sequence[str] | None = '49b4f475d58a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('charter_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('content_md', sa.Text(), nullable=False),
    sa.Column('rules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'version', name='uq_charter_user_version')
    )
    op.create_index(op.f('ix_charter_versions_user_id'), 'charter_versions', ['user_id'], unique=False)
    op.create_table('account_cash_flows',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('account_id', sa.UUID(), nullable=False),
    sa.Column('broker_activity_id', sa.String(length=64), nullable=False),
    sa.Column('flow_type', sa.String(length=16), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=6), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'broker_activity_id', name='uq_cash_flow_user_activity')
    )
    op.create_index(op.f('ix_account_cash_flows_account_id'), 'account_cash_flows', ['account_id'], unique=False)
    op.create_index(op.f('ix_account_cash_flows_broker_activity_id'), 'account_cash_flows', ['broker_activity_id'], unique=False)
    op.create_index(op.f('ix_account_cash_flows_occurred_at'), 'account_cash_flows', ['occurred_at'], unique=False)
    op.create_index(op.f('ix_account_cash_flows_user_id'), 'account_cash_flows', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_account_cash_flows_user_id'), table_name='account_cash_flows')
    op.drop_index(op.f('ix_account_cash_flows_occurred_at'), table_name='account_cash_flows')
    op.drop_index(op.f('ix_account_cash_flows_broker_activity_id'), table_name='account_cash_flows')
    op.drop_index(op.f('ix_account_cash_flows_account_id'), table_name='account_cash_flows')
    op.drop_table('account_cash_flows')
    op.drop_index(op.f('ix_charter_versions_user_id'), table_name='charter_versions')
    op.drop_table('charter_versions')
