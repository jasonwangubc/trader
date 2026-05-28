"""backtest_signal_scans + backtest_signal_candidates (Phase-1 cache)

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-05-27 18:00:00.000000

"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "j1k2l3m4n5o6"
down_revision: str | Sequence[str] | None = "i0j1k2l3m4n5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_signal_scans",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("lookback_days", sa.Integer, nullable=False),
        sa.Column("symbols_scanned", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_backtest_signal_scans_lookback_days", "backtest_signal_scans", ["lookback_days"])
    op.create_index("ix_backtest_signal_scans_status", "backtest_signal_scans", ["status"])

    op.create_table(
        "backtest_signal_candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("scan_id", UUID(as_uuid=True), sa.ForeignKey("backtest_signal_scans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("signal_date", sa.DateTime(timezone=False), nullable=False),
        sa.Column("bar_index", sa.Integer, nullable=False),
        sa.Column("tt_score", sa.Integer, nullable=False),
        sa.Column("vcp_score", sa.Numeric(4, 3), nullable=False),
        sa.Column("pattern_type", sa.String(24), nullable=False),
        sa.Column("pattern_quality", sa.Numeric(4, 3), nullable=False),
        sa.Column("buyability", sa.String(16), nullable=False),
        sa.Column("pivot_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("atr_at_signal", sa.Numeric(18, 6), nullable=False),
    )
    op.create_index("ix_backtest_candidates_scan_id", "backtest_signal_candidates", ["scan_id"])
    op.create_index("ix_backtest_candidates_symbol", "backtest_signal_candidates", ["symbol"])
    op.create_index("ix_backtest_candidates_scan_symbol", "backtest_signal_candidates", ["scan_id", "symbol"])


def downgrade() -> None:
    op.drop_table("backtest_signal_candidates")
    op.drop_table("backtest_signal_scans")
