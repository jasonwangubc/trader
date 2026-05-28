"""broker_executions + broker_trades + broker_sync_state

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-05-27 16:00:00.000000

"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "i0j1k2l3m4n5"
down_revision: str | Sequence[str] | None = "h9i0j1k2l3m4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broker_executions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False, server_default="user_default"),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("broker_execution_id", sa.String(64), nullable=False),
        sa.Column("broker_order_id", sa.String(64), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("commission", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue", sa.String(16), nullable=True),
        sa.Column("raw", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "broker_execution_id", name="uq_broker_exec_user_id"),
    )
    op.create_index("ix_broker_executions_user_id",     "broker_executions", ["user_id"])
    op.create_index("ix_broker_executions_account_id",  "broker_executions", ["account_id"])
    op.create_index("ix_broker_executions_symbol",      "broker_executions", ["symbol"])
    op.create_index("ix_broker_executions_executed_at", "broker_executions", ["executed_at"])
    op.create_index("ix_broker_executions_order_id",    "broker_executions", ["broker_order_id"])
    op.create_index("ix_broker_exec_user_symbol_time",  "broker_executions", ["user_id", "symbol", "executed_at"])

    op.create_table(
        "broker_trades",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False, server_default="user_default"),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("shares", sa.Numeric(18, 4), nullable=False),
        sa.Column("avg_entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("avg_exit_price",  sa.Numeric(18, 6), nullable=False),
        sa.Column("entry_commission", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("exit_commission",  sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("entry_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exit_date",  sa.DateTime(timezone=True), nullable=False),
        sa.Column("hold_days",  sa.Integer, nullable=False, server_default="0"),
        sa.Column("realized_pnl",      sa.Numeric(18, 6), nullable=False),
        sa.Column("realized_pnl_pct",  sa.Numeric(10, 4), nullable=True),
        sa.Column("r_multiple",        sa.Numeric(8, 3),  nullable=True),
        sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("setup_type", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("close_reason_tag", sa.String(64), nullable=True),
        sa.Column("notes", sa.String(2000), nullable=True),
        sa.Column("entry_execution_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("exit_execution_ids",  JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_broker_trades_user_id",      "broker_trades", ["user_id"])
    op.create_index("ix_broker_trades_account_id",   "broker_trades", ["account_id"])
    op.create_index("ix_broker_trades_symbol",       "broker_trades", ["symbol"])
    op.create_index("ix_broker_trades_entry_date",   "broker_trades", ["entry_date"])
    op.create_index("ix_broker_trades_exit_date",    "broker_trades", ["exit_date"])
    op.create_index("ix_broker_trades_ticket_id",    "broker_trades", ["ticket_id"])
    op.create_index("ix_broker_trades_user_exit",    "broker_trades", ["user_id", "exit_date"])
    op.create_index("ix_broker_trades_user_symbol",  "broker_trades", ["user_id", "symbol"])

    op.create_table(
        "broker_sync_state",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(128), nullable=False, server_default="user_default"),
        sa.Column("account_id", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_synced_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(16), nullable=False, server_default="idle"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "account_id", name="uq_broker_sync_user_account"),
    )
    op.create_index("ix_broker_sync_state_user_id",    "broker_sync_state", ["user_id"])
    op.create_index("ix_broker_sync_state_account_id", "broker_sync_state", ["account_id"])


def downgrade() -> None:
    op.drop_table("broker_sync_state")
    op.drop_table("broker_trades")
    op.drop_table("broker_executions")
