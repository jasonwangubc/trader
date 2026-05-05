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

    @abstractmethod
    async def cancel_order(self, account_id: str, broker_order_id: str) -> None: ...

    @abstractmethod
    async def get_order(self, account_id: str, broker_order_id: str) -> BrokerOrderAck: ...
