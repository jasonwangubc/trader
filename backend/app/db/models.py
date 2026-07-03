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
    Text,
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


class EquitySnapshot(Base):
    """One equity data point per (account, currency, day).

    AccountBalance is overwrite-on-sync (current state only) and Questrade has
    no historical-balances endpoint — so account history builds forward from
    these rows. They power the drawdown circuit breaker (peak equity) and the
    charter honesty page (actual equity curve vs benchmark counterfactual).

    source: "sync" (user-triggered accounts sync), "nightly" (from cached
    balances — may be stale if the user hasn't synced), "manual_seed" (user
    backfill of a known historical value).
    """
    __tablename__ = "equity_snapshots"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    cash: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    market_value: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    total_equity: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    source: Mapped[str] = mapped_column(String(16), default="sync")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "account_id", "currency", "snapshot_date",
            name="uq_equity_snapshot_account_ccy_date",
        ),
    )


class CharterVersion(Base):
    """One immutable version of the user's trading charter (pre-commitment).

    APPEND-ONLY by design: there are no update or delete endpoints. The whole
    point of a charter is that its rules were written *before* the drawdown —
    revising it must leave a visible trail (new version + audit event), never
    a silent rewrite. Active version = highest version number.
    """
    __tablename__ = "charter_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    version: Mapped[int] = mapped_column(Integer)
    content_md: Mapped[str] = mapped_column(Text)          # the written plan, markdown
    # Structured, machine-checkable rules: evaluation horizon, review cadence,
    # kill/scale criteria, and the guardrail thresholds this charter locks.
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)  # why this revision
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "version", name="uq_charter_user_version"),
    )


class AccountCashFlow(Base):
    """External cash movement (deposit / withdrawal / transfer) from broker
    activities. Powers the honesty page's deposit-timing-matched benchmark:
    every deposit is replayed into a buy-and-hold index counterfactual.

    Dedup: synthetic SHA-1 id per activity (same pattern as broker_executions).
    """
    __tablename__ = "account_cash_flows"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    broker_activity_id: Mapped[str] = mapped_column(String(64), index=True)
    flow_type: Mapped[str] = mapped_column(String(16))      # deposit | withdrawal | transfer
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(_money)          # signed: + inflow, − outflow
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    description: Mapped[str | None] = mapped_column(String(200), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "broker_activity_id", name="uq_cash_flow_user_activity"),
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


class WheelCandidate(Base):
    """Snapshotted wheel-strategy candidate (CSP or CC) from the latest scan.

    Refreshed by /api/wheel/scan. Per-user; one row per (symbol, strategy, expiry, strike).
    The whole table is rewritten on each scan for the scanning user (delete-then-insert).
    """
    __tablename__ = "wheel_candidates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    strategy: Mapped[str] = mapped_column(String(16))   # "csp" | "cc"

    # Underlying snapshot at scan time
    last_price: Mapped[Decimal] = mapped_column(_money)

    # Contract
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    dte: Mapped[int] = mapped_column(Integer)
    strike: Mapped[Decimal] = mapped_column(_money)
    option_type: Mapped[str] = mapped_column(String(4))  # "put" | "call"

    # Quote
    bid: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    mid: Mapped[Decimal] = mapped_column(_money)
    last: Mapped[Decimal | None] = mapped_column(_money, nullable=True)
    bid_ask_spread_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)

    # Liquidity
    open_interest: Mapped[int] = mapped_column(Integer, default=0)
    volume: Mapped[int] = mapped_column(Integer, default=0)

    # Greeks / IV (yfinance provides IV; delta is approximated from moneyness)
    implied_volatility: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    delta_approx: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    # Yield math
    premium_yield_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))         # mid / capital_at_risk
    annualized_yield_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))      # × 365/dte
    otm_pct: Mapped[Decimal] = mapped_column(Numeric(8, 4))                   # |strike-spot|/spot
    capital_at_risk: Mapped[Decimal] = mapped_column(_money)                  # CSP: strike*100; CC: last*100
    breakeven: Mapped[Decimal] = mapped_column(_money)

    # Risk flags
    earnings_before_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    next_earnings_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)

    # Composite ranking score (0-100)
    score: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal(0), index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, default=dict)

    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        Index("ix_wheel_candidates_user_score", "user_id", "score"),
    )


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


class BacktestSignalScan(Base):
    """One execution of the heavy Phase-1 signal scan over the symbol universe.

    A scan is a snapshot of every (symbol, bar) where the pattern detector
    found anything — independent of the trade-simulation parameters (stop,
    target, time-stop, thresholds). Cheap parameter sweeps reuse the same
    scan by filtering the candidates and re-running Phase-2 trade simulation.

    Status lifecycle: running → success | failed. Stale "running" rows older
    than a few hours can be safely ignored (no crash recovery in v1).
    """
    __tablename__ = "backtest_signal_scans"

    id: Mapped[uuid.UUID] = _uuid_pk()
    lookback_days: Mapped[int] = mapped_column(Integer, index=True)
    symbols_scanned: Mapped[int] = mapped_column(Integer, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BacktestSignalCandidate(Base):
    """One pattern-match bar from a scan. Phase-2 (trade sim) filters these by
    threshold and walks the bars forward to compute outcomes.

    Cached regardless of pattern_quality / tt_score so sweeps over those
    thresholds don't require re-scanning. ATR snapshot at the signal bar is
    stored so we don't recompute it during stop calc.
    """
    __tablename__ = "backtest_signal_candidates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("backtest_signal_scans.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    signal_date: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    bar_index: Mapped[int] = mapped_column(Integer)   # position within the symbol's loaded bar series

    tt_score: Mapped[int] = mapped_column(Integer)
    vcp_score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    pattern_type: Mapped[str] = mapped_column(String(24))
    pattern_quality: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    buyability: Mapped[str] = mapped_column(String(16))
    pivot_price: Mapped[Decimal] = mapped_column(_money)
    atr_at_signal: Mapped[Decimal] = mapped_column(_money)

    __table_args__ = (
        Index("ix_backtest_candidates_scan_symbol", "scan_id", "symbol"),
    )


class BrokerExecution(Base):
    """Raw fill from the broker, regardless of source (manual UI, system-placed, mobile).

    This is the authoritative record of every trade that actually happened in
    the brokerage account. Stored verbatim and never edited — the FIFO matcher
    derives BrokerTrade rows from these.

    Dedup key: (user_id, broker_execution_id). Re-syncing the same window is
    idempotent.
    """
    __tablename__ = "broker_executions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )

    broker_execution_id: Mapped[str] = mapped_column(String(64), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    side: Mapped[str] = mapped_column(String(8))     # "buy" | "sell"
    quantity: Mapped[Decimal] = mapped_column(_qty)
    price: Mapped[Decimal] = mapped_column(_money)
    commission: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    venue: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "broker_execution_id", name="uq_broker_exec_user_id"),
        Index("ix_broker_exec_user_symbol_time", "user_id", "symbol", "executed_at"),
    )


class BrokerTrade(Base, TimestampMixin):
    """Derived round-trip trade: one row per sell execution, FIFO-matched to
    its source buy lot(s).

    Built by services.broker_history_service.rebuild_trades_for_user from the
    immutable BrokerExecution rows. Re-runnable: deletes and rebuilds.

    A "trade" here is the smallest meaningful unit a journal cares about: how
    much you closed at one moment, what you paid for those shares originally,
    the P&L, and how long you held them. Scale-outs naturally appear as
    multiple trades on the same symbol.
    """
    __tablename__ = "broker_trades"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )

    symbol: Mapped[str] = mapped_column(String(32), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    shares: Mapped[Decimal] = mapped_column(_qty)              # qty closed in this round-trip
    avg_entry_price: Mapped[Decimal] = mapped_column(_money)   # weighted by per-lot qty
    avg_exit_price: Mapped[Decimal] = mapped_column(_money)
    entry_commission: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))  # allocated portion
    exit_commission: Mapped[Decimal] = mapped_column(_money, default=Decimal(0))

    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    exit_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    hold_days: Mapped[int] = mapped_column(Integer, default=0)

    realized_pnl: Mapped[Decimal] = mapped_column(_money)      # in trade currency, net of commissions
    realized_pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)  # if a stop is known

    # Reconciliation
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    setup_type: Mapped[str] = mapped_column(String(32), default=SetupType.MANUAL.value)
    close_reason_tag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Traceability — execution ids that contributed to this round-trip
    entry_execution_ids: Mapped[list] = mapped_column(JSONB, default=list)
    exit_execution_ids: Mapped[list] = mapped_column(JSONB, default=list)

    __table_args__ = (
        Index("ix_broker_trades_user_exit", "user_id", "exit_date"),
        Index("ix_broker_trades_user_symbol", "user_id", "symbol"),
    )


class BrokerSyncState(Base):
    """Per-account high-water mark for incremental execution sync."""
    __tablename__ = "broker_sync_state"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=USER_DEFAULT)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    last_synced_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str] = mapped_column(String(16), default="idle")  # idle | running | success | failed
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "account_id", name="uq_broker_sync_user_account"),
    )


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
