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
    BigInteger,
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

USER_DEFAULT = "user_default"   # placeholder for data created before multi-tenancy


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    questrade_account_id: Mapped[str] = mapped_column(String(64), index=True)
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
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
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
    close_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    close_reason_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Exit ladder: [{price, shares, reason}] — partial exits at multiple levels
    exit_plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    orders: Mapped[list[Order]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    positions: Mapped[list[Position]] = relationship(back_populates="ticket")


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
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
    """Win/loss streak per user for risk-multiplier scaling.
    Primary key is user_id (one row per user, previously singleton id=1).
    """
    __tablename__ = "streak_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, default=USER_DEFAULT)
    consecutive_wins: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0)
    last_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_multiplier: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OptionStrategy(StrEnum):
    COVERED_CALL  = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"
    PROTECTIVE_PUT = "protective_put"


class OptionStatus(StrEnum):
    OPEN      = "open"
    CLOSED    = "closed"       # bought back or sold
    EXPIRED   = "expired"      # expired worthless
    ASSIGNED  = "assigned"     # exercised / assigned


class OptionTicket(Base, TimestampMixin):
    """An options income / hedge position ticket (CC, CSP, protective put)."""
    __tablename__ = "option_tickets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), index=True
    )
    underlying_symbol: Mapped[str] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    strategy: Mapped[str] = mapped_column(String(32))          # OptionStrategy
    option_type: Mapped[str] = mapped_column(String(4))        # "call" | "put"
    strike_price: Mapped[Decimal] = mapped_column(_money)
    expiry_date: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    contracts: Mapped[int] = mapped_column(Integer, default=1)  # 1 contract = 100 shares
    premium_received: Mapped[Decimal] = mapped_column(_money)   # per share (so × 100 × contracts for total)
    break_even: Mapped[Decimal | None] = mapped_column(_money, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=OptionStatus.OPEN.value, index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    thesis: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Closing details
    premium_paid_to_close: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(_money, nullable=True)  # total, not per share

    # Link to underlying position (for CC) or associated regular ticket
    position_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )


class DailyBar(Base):
    """End-of-day OHLCV bar, adjusted for splits/dividends. Source: yfinance."""
    __tablename__ = "daily_bars"

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    bar_date: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    open: Mapped[Decimal] = mapped_column(_money)
    high: Mapped[Decimal] = mapped_column(_money)
    low: Mapped[Decimal] = mapped_column(_money)
    close: Mapped[Decimal] = mapped_column(_money)
    # BIGINT — penny stocks can do 1B+ shares on event days (e.g. BYND 2025-10-21)
    volume: Mapped[int] = mapped_column(BigInteger)
    adj_close: Mapped[Decimal] = mapped_column(_money)

    __table_args__ = (
        UniqueConstraint("symbol", "bar_date", name="uq_daily_bar_symbol_date"),
        Index("ix_daily_bar_symbol_date", "symbol", "bar_date"),
    )


class EarningsDate(Base):
    """Next (and recent) earnings dates per symbol, synced from yfinance."""
    __tablename__ = "earnings_dates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    next_earnings_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_eps_surprise_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    last_earnings_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    avg_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # 50d avg volume for PEP calc
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = ()


class ScreenerSymbol(Base, TimestampMixin):
    """Symbols on the active watchlist for nightly screening."""
    __tablename__ = "screener_symbols"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ScreenerScore(Base):
    """Latest scoring result per symbol. One row per symbol; overwritten on each run."""
    __tablename__ = "screener_scores"

    id: Mapped[uuid.UUID] = _uuid_pk()
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Universe metadata
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    universe_source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # sp500|nasdaq100|tsx60|manual

    # Trend Template: 0-8 integer, one point per passing criterion
    tt_score: Mapped[int] = mapped_column(Integer, default=0)
    tt_criteria: Mapped[dict] = mapped_column(JSONB, default=dict)

    # VCP: 0.0-1.0 float, heuristic likelihood score
    vcp_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal(0))
    vcp_details: Mapped[dict] = mapped_column(JSONB, default=dict)

    # Relative strength vs SPY/XIU (0-99 percentile within screener universe)
    rs_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rs_raw: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Price data snapshot at scoring time
    last_close: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    ma_50: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    ma_150: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    ma_200: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    high_52w: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    low_52w: Mapped[Decimal | None] = mapped_column(_money, nullable=True)

    # Fundamentals (from yfinance ticker.info — covers US + Canadian)
    fundamental_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal(0))
    revenue_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    net_income_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)  # most recent quarter YoY
    earnings_annual_growth: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)  # TTM/annual YoY
    net_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    roe: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    eps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    fundamental_error: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # IBD-style percentile rankings (0-99 within scored universe)
    eps_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)   # earnings (qtrly EPS growth + TTM EPS)
    smr_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)   # sales / margin / ROE composite

    # Pattern + buyability — distinguishes "leader to watch" from "setup to act on".
    # Composite is heavily penalized (or zeroed) when buyability == "extended".
    pattern_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    pattern_quality: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    buyability: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    pivot_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    base_low: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    base_length_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_depth_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    extension_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)

    # Composite score — weighted blend, zeroed if buyability == "extended" or "broken"
    composite_score: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=Decimal(0))


class TrailingAction(Base, TimestampMixin):
    """Pending coaching action created when a trailing milestone or exit ladder leg is hit.

    Lifecycle: pending → (user confirms) → executed | (user dismisses) → dismissed
    The monitor creates these; the API + frontend let the user act on them.
    """
    __tablename__ = "trailing_actions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), index=True
    )

    # "trail_stop" — move the GTC stop to a new level
    # "scale_out"  — sell a portion of the position at a target price
    action_type: Mapped[str] = mapped_column(String(32))

    # Which milestone triggered this (e.g. "+1R", "+5R", "T1 +1.5R")
    milestone: Mapped[str] = mapped_column(String(32))

    # trail_stop fields
    old_stop: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    new_stop: Mapped[Decimal | None] = mapped_column(_money, nullable=True)

    # scale_out fields
    sell_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    sell_shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leg_label: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Context at trigger time
    open_r: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal(0))
    triggered_price: Mapped[Decimal] = mapped_column(_money)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Lifecycle: pending → executed | dismissed | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_price: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(String(500), nullable=True)

    ticket: Mapped["Ticket"] = relationship(backref="trailing_actions")


class AuditLog(Base):
    """Append-only event log. Every state change in the system goes here."""
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
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
