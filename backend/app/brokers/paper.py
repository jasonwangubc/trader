"""PaperBroker: wraps a live quote source for triggers/prices but intercepts
orders and simulates instant fills at the trigger price. No slippage model in MVP."""
from __future__ import annotations

import uuid as _uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal

from app.brokers.base import (
    BrokerAccount,
    BrokerBalance,
    BrokerInterface,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerPosition,
    BrokerQuote,
)

# In-process store for simulated orders so get_order() works.
_paper_orders: dict[str, BrokerOrderAck] = {}


class PaperBroker(BrokerInterface):
    name = "paper"

    def __init__(self, quote_source: BrokerInterface | None = None) -> None:
        self._quote_source = quote_source

    # ---- read-side: delegate to live broker ----

    async def list_accounts(self) -> list[BrokerAccount]:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return await self._quote_source.list_accounts()

    async def get_balances(self, account_id: str) -> list[BrokerBalance]:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return await self._quote_source.get_balances(account_id)

    async def get_positions(self, account_id: str) -> list[BrokerPosition]:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return await self._quote_source.get_positions(account_id)

    async def get_quote(self, symbol: str) -> BrokerQuote:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return await self._quote_source.get_quote(symbol)

    async def get_quotes_batch(self, symbols: list[str]) -> dict[str, BrokerQuote]:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return await self._quote_source.get_quotes_batch(symbols)

    def stream_quotes(self, symbols: list[str]) -> AsyncIterator[BrokerQuote]:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return self._quote_source.stream_quotes(symbols)

    # ---- orders: simulated ----

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderAck:
        """Simulate an instant fill at limit_price or stop_price (whichever is set),
        falling back to 0 so the order_service can substitute the last quote price."""
        order_id = f"paper-{_uuid.uuid4().hex[:12]}"
        fill_price = req.limit_price or req.stop_price or Decimal(0)
        ack = BrokerOrderAck(
            broker_order_id=order_id,
            status="filled",
            submitted_at=datetime.now(timezone.utc),
            fill_price=fill_price if fill_price > 0 else None,
            fill_quantity=req.quantity,
        )
        _paper_orders[order_id] = ack
        return ack

    async def get_order(self, account_id: str, broker_order_id: str) -> BrokerOrderAck:
        ack = _paper_orders.get(broker_order_id)
        if ack is None:
            raise RuntimeError(f"Paper order {broker_order_id} not found")
        return ack

    async def cancel_order(self, account_id: str, broker_order_id: str) -> None:
        _paper_orders.pop(broker_order_id, None)
