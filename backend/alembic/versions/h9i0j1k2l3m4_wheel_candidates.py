"""wheel_candidates table

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-05-22
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "h9i0j1k2l3m4"
down_revision: str | Sequence[str] | None = "g8h9i0j1k2l3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wheel_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False, server_default="user_default"),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("strategy", sa.String(16), nullable=False),
        sa.Column("last_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("expiry", sa.DateTime(timezone=False), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("strike", sa.Numeric(18, 6), nullable=False),
        sa.Column("option_type", sa.String(4), nullable=False),
        sa.Column("bid", sa.Numeric(18, 6), nullable=True),
        sa.Column("ask", sa.Numeric(18, 6), nullable=True),
        sa.Column("mid", sa.Numeric(18, 6), nullable=False),
        sa.Column("last", sa.Numeric(18, 6), nullable=True),
        sa.Column("bid_ask_spread_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column("open_interest", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("volume", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("implied_volatility", sa.Numeric(8, 4), nullable=True),
        sa.Column("delta_approx", sa.Numeric(6, 4), nullable=True),
        sa.Column("premium_yield_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("annualized_yield_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("otm_pct", sa.Numeric(8, 4), nullable=False),
        sa.Column("capital_at_risk", sa.Numeric(18, 6), nullable=False),
        sa.Column("breakeven", sa.Numeric(18, 6), nullable=False),
        sa.Column("earnings_before_expiry", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("next_earnings_date", sa.DateTime(timezone=False), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), nullable=False, server_default="0"),
        sa.Column("score_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_wheel_candidates_user_id", "wheel_candidates", ["user_id"])
    op.create_index("ix_wheel_candidates_symbol", "wheel_candidates", ["symbol"])
    op.create_index("ix_wheel_candidates_score", "wheel_candidates", ["score"])
    op.create_index("ix_wheel_candidates_scanned_at", "wheel_candidates", ["scanned_at"])
    op.create_index("ix_wheel_candidates_user_score", "wheel_candidates", ["user_id", "score"])


def downgrade() -> None:
    op.drop_index("ix_wheel_candidates_user_score", table_name="wheel_candidates")
    op.drop_index("ix_wheel_candidates_scanned_at", table_name="wheel_candidates")
    op.drop_index("ix_wheel_candidates_score", table_name="wheel_candidates")
    op.drop_index("ix_wheel_candidates_symbol", table_name="wheel_candidates")
    op.drop_index("ix_wheel_candidates_user_id", table_name="wheel_candidates")
    op.drop_table("wheel_candidates")
