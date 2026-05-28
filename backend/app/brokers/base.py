"""Broker abstraction. Every order flows through BrokerInterface so we can swap real/paper
or migrate to a different broker (IBKR, Alpaca) without touching the rest of the system.

Sprint 1 only requires the read-side (accounts, positions, balances). Order placement and
streaming will be implemented in Sprint 2; the interface declares them now so call sites
can be designed against the final shape.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class BrokerAccount:
    broker_account_id: str
    type: str
    primary_currency: str


@dataclass(frozen=True)
class BrokerBalance:
    account_id: str
    currency: str
    cash: Decimal
    market_value: Decimal
    total_equity: Decimal
    buying_power: Decimal
    maintenance_excess: Decimal | None = None


@dataclass(frozen=True)
class BrokerPosition:
    account_id: str
    symbol: str
    currency: str
    quantity: Decimal
    avg_cost: Decimal
    current_price: Decimal | None
    market_value: Decimal
    open_pnl: Decimal


@dataclass(frozen=True)
class BrokerOrderRequest:
    account_id: str
    symbol: str
    side: str  # "buy" / "sell"
    order_type: str  # "market" / "limit" / "stop_market" / "stop_limit"
    quantity: int
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: str = "Day"


@dataclass(frozen=True)
class BrokerOrderAck:
    broker_order_id: str
    status: str           # pending | submitted | accepted | partial | filled | cancelled | rejected
    submitted_at: datetime
    fill_price: Decimal | None = None    # set when status == filled
    fill_quantity: int | None = None


@dataclass(frozen=True)
class BrokerBracketRequest:
    """Atomic entry + stop bracket. Stop activates server-side once entry fills.

    Primary is a stop-limit buy (matches what we'd send for a non-bracket entry).
    StopLoss is a stop-market sell GTC — same protection profile as the legacy
    sequential stop, but placed in the same API call so there's no naked window.
    """
    account_id: str
    symbol: str
    quantity: int
    entry_stop_price: Decimal
    entry_limit_price: Decimal
    stop_loss_price: Decimal
    entry_time_in_force: str = "Day"
    stop_loss_time_in_force: str = "GoodTillCancelled"


@dataclass(frozen=True)
class BrokerBracketAck:
    primary: BrokerOrderAck
    stop_loss: BrokerOrderAck


@dataclass(frozen=True)
class BrokerOpenOrder:
    """Currently-pending order at the broker (any source — manual or system-placed)."""
    broker_order_id: str
    account_id: str
    symbol: str
    currency: str
    side: str               # "buy" / "sell"
    order_type: str         # "market" / "limit" / "stop_market" / "stop_limit"
    quantity: int           # remaining open quantity (totalQuantity - filledQuantity)
    limit_price: Decimal | None
    stop_price: Decimal | None
    submitted_at: datetime | None
    status: str             # pending | accepted | partial


@dataclass(frozen=True)
class BrokerExecution:
    """One fill at the broker, regardless of source (manual UI, system-placed, mobile).

    This is the journal source-of-truth: every trade you actually did, including
    the ones you placed by hand outside our ticket system.
    """
    broker_execution_id: str          # unique fill id at the broker (dedup key)
    broker_order_id: str | None       # parent order id (links to BrokerOpenOrder / our orders)
    account_id: str
    symbol: str
    currency: str
    side: str                         # "buy" | "sell"
    quantity: Decimal                 # Questrade can report fractional in some products
    price: Decimal
    commission: Decimal               # total per-fill cost (commission + fees)
    executed_at: datetime             # UTC
    venue: str | None = None
    raw: dict | None = None           # full broker payload for forensics


@dataclass(frozen=True)
class BrokerQuote:
    symbol: str
    last: Decimal
    bid: Decimal | None
    ask: Decimal | None
    volume: int | None
    at: datetime
    delay: int = 0  # minutes of quote delay; 0 = real-time


class BrokerInterface(ABC):
    """All operations are async; implementations may use HTTP, WebSocket, or simulate."""

    name: str

    # ---- Read ----
    @abstractmethod
    async def list_accounts(self) -> list[BrokerAccount]: ...

    @abstractmethod
    async def get_balances(self, account_id: str) -> list[BrokerBalance]: ...

    @abstractmethod
    async def get_positions(self, account_id: str) -> list[BrokerPosition]: ...

    # ---- Quotes ----
    @abstractmethod
    async def get_quote(self, symbol: str) -> BrokerQuote: ...

    async def get_quotes_batch(self, symbols: list[str]) -> dict[str, BrokerQuote]:
        """Fetch multiple quotes. Default: sequential; override for efficiency."""
        out: dict[str, BrokerQuote] = {}
        for sym in symbols:
            try:
                out[sym] = await self.get_quote(sym)
            except Exception:
                pass
        return out

    @abstractmethod
    def stream_quotes(self, symbols: list[str]) -> AsyncIterator[BrokerQuote]: ...

    # ---- Orders ----
    @abstractmethod
    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderAck: ...

    async def place_bracket_order(self, req: BrokerBracketRequest) -> BrokerBracketAck:
        """Place an atomic entry+stop bracket. Default impl raises; brokers that
        support it should override. Callers should fall back to sequential
        place_order calls if this raises NotImplementedError."""
        raise NotImplementedError(f"{self.name} does not support bracket orders")

    @abstractmethod
    async def cancel_order(self, account_id: str, broker_order_id: str) -> None: ...

    @abstractmethod
    async def get_order(self, account_id: str, broker_order_id: str) -> BrokerOrderAck: ...

    async def get_open_orders(self, account_id: str) -> list[BrokerOpenOrder]:
        """Return all currently-pending orders at the broker for this account.

        Default: empty list — useful for paper or limited brokers that don't
        expose order listing. Real broker implementations should override.
        """
        return []

    async def get_executions(
        self,
        account_id: str,
        start: datetime,
        end: datetime,
    ) -> list[BrokerExecution]:
        """Return every fill in [start, end] for this account, regardless of source.

        Brokers without an executions endpoint should return [] — the sync
        service will treat that as "no history available". Real brokers should
        override.

        Note: Questrade's /executions has ~30-day retention. For historical
        backfills, prefer `get_activities` if the broker provides one.
        """
        return []

    async def get_activities(
        self,
        account_id: str,
        start: datetime,
        end: datetime,
    ) -> list[BrokerExecution]:
        """Return every Trade-type activity in [start, end] for this account.

        For Questrade this hits /v1/accounts/:id/activities and filters to
        Trades; goes back to account opening (unlike /executions). Brokers
        without an activities concept should leave this returning [] and rely
        on get_executions.
        """
        return []
