"""ORM models. Single file for now; split later if it grows past ~500 lines."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum


class StrEnum(str, Enum):
    """Lightweight 3.10-compatible string enum (StrEnum is 3.11+)."""

    def __str__(self) -> str:
        return self.value

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, utcnow


# ---------- Enums (stored as strings; values are stable contracts) ----------

class AccountType(StrEnum):
    TFSA = "TFSA"
    RRSP = "RRSP"
    MARGIN = "Margin"
    CASH = "Cash"
    LIRA = "LIRA"
    RESP = "RESP"
    FHSA = "FHSA"
    OTHER = "Other"


class Currency(StrEnum):
    CAD = "CAD"
    USD = "USD"


class TicketStatus(StrEnum):
    DRAFT = "draft"
    ARMED = "armed"
    TRIGGERED = "triggered"
    FILLED = "filled"
    STOPPED_OUT = "stopped_out"
    TARGET_HIT = "target_hit"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TriggerType(StrEnum):
    PRICE_ABOVE = "price_above"
    PRICE_ABOVE_WITH_VOLUME = "price_above_with_volume"
    DAY_CLOSE_ABOVE = "day_close_above"


class SetupType(StrEnum):
    VCP = "VCP"
    FLAT_BASE = "flat_base"
    EP = "ep"
    CUP_HANDLE = "cup_handle"
    PIVOT = "pivot"
    MANUAL = "manual"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderIntent(StrEnum):
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    EXIT = "exit"
    SCALE_OUT = "scale_out"


class TradeOutcome(StrEnum):
    WIN = "win"
    LOSS = "loss"
    SCRATCH = "scratch"


# ---------- Helpers ----------

def _uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


_money = Numeric(18, 6)
_qty = Numeric(18, 4)


# ---------- Models ----------

class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    questrade_account_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(32))  # AccountType
    primary_currency: Mapped[str] = mapped_column(String(3))  # Currency
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    real_money_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    balances: Mapped[list[AccountBalance]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    positions: Mapped[list[Position]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class AccountBalance(Base):
    """Per-currency cash/equity snapshot for an account. Synced from broker."""
    __tablename__ = "account_balances"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    cash: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    market_value: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    total_equity: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    buying_power: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    maintenance_excess: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship(back_populates="balances")

    __table_args__ = (
        UniqueConstraint("account_id", "currency", name="uq_account_balance_currency"),
    )


class Position(Base, TimestampMixin):
    """Snapshot of a held position. Synced from broker; one row per (account, symbol, currency)."""
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[Decimal] = mapped_column(_qty, default=Decimal(0))
    avg_cost: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    current_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    market_value: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    open_pnl: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))

    is_managed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_buy_and_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )

    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship(back_populates="positions")
    ticket: Mapped[Ticket | None] = relationship(back_populates="positions")

    __table_args__ = (
        UniqueConstraint("account_id", "symbol", "currency", name="uq_position_account_symbol_ccy"),
    )


class Ticket(Base, TimestampMixin):
    """A pre-trade ticket: setup, trigger, stop, target, position size — committed before execution."""
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), index=True
    )

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    setup_type: Mapped[str] = mapped_column(String(32))  # SetupType

    # Trigger
    trigger_type: Mapped[str] = mapped_column(String(32))  # TriggerType
    trigger_price: Mapped[Decimal] = mapped_column(_money)
    volume_confirm_multiple: Mapped[float | None] = mapped_column(nullable=True)

    # Risk management
    stop_price: Mapped[Decimal] = mapped_column(_money)
    target_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    time_stop_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Sizing (snapshotted at creation; do not recompute later)
    risk_pct: Mapped[Decimal] = mapped_column(Numeric(6, 5))  # e.g. 0.00750
    risk_amount: Mapped[Decimal] = mapped_column(_money)
    household_equity_at_creation: Mapped[Decimal] = mapped_column(_money)
    streak_multiplier_at_creation: Mapped[Decimal] = mapped_column(Numeric(4, 2))
    position_size_shares: Mapped[int] = mapped_column(Integer)
    position_size_value: Mapped[Decimal] = mapped_column(_money)

    # Lifecycle
    status: Mapped[str] = mapped_column(String(32), default=TicketStatus.DRAFT, index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Post-trade
    realized_pnl: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)  # TradeOutcome

    thesis: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(4000), nullable=True)

    orders: Mapped[list[Order]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    positions: Mapped[list[Position]] = relationship(back_populates="ticket")


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), index=True, nullable=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), index=True
    )
    questrade_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    side: Mapped[str] = mapped_column(String(8))  # OrderSide
    order_type: Mapped[str] = mapped_column(String(16))  # OrderType
    intent: Mapped[str] = mapped_column(String(32))  # OrderIntent

    quantity: Mapped[int] = mapped_column(Integer)
    limit_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.PENDING, index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    ticket: Mapped[Ticket | None] = relationship(back_populates="orders")
    fills: Mapped[list[Fill]] = relationship(back_populates="order", cascade="all, delete-orphan")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = _uuid_pk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), index=True, nullable=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(_money)
    commission: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False)

    order: Mapped[Order] = relationship(back_populates="fills")


class Setting(Base):
    """Key-value runtime settings. JSON values for flexibility (numbers, dicts, lists)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StreakState(Base):
    """Singleton row tracking current win/loss streak for risk-multiplier scaling."""
    __tablename__ = "streak_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    consecutive_wins: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    last_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_multiplier: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLog(Base):
    """Append-only event log. Every state change in the system goes here."""
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String(32))  # user, system, broker, monitor
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
    )
