"""PaperBroker: wraps a live quote source for triggers/prices but intercepts orders
and simulates fills with modeled slippage. Stub for Sprint 1; flesh out in Sprint 2."""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.brokers.base import (
    BrokerAccount,
    BrokerBalance,
    BrokerInterface,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerPosition,
    BrokerQuote,
)


class PaperBroker(BrokerInterface):
    name = "paper"

    def __init__(self, quote_source: BrokerInterface | None = None) -> None:
        self._quote_source = quote_source

    async def list_accounts(self) -> list[BrokerAccount]:
        raise NotImplementedError("PaperBroker.list_accounts — Sprint 2")

    async def get_balances(self, account_id: str) -> list[BrokerBalance]:
        raise NotImplementedError("PaperBroker.get_balances — Sprint 2")

    async def get_positions(self, account_id: str) -> list[BrokerPosition]:
        raise NotImplementedError("PaperBroker.get_positions — Sprint 2")

    async def get_quote(self, symbol: str) -> BrokerQuote:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return await self._quote_source.get_quote(symbol)

    def stream_quotes(self, symbols: list[str]) -> AsyncIterator[BrokerQuote]:
        if self._quote_source is None:
            raise RuntimeError("PaperBroker has no quote source configured")
        return self._quote_source.stream_quotes(symbols)

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderAck:
        raise NotImplementedError("PaperBroker.place_order — Sprint 2")

    async def cancel_order(self, broker_order_id: str) -> None:
        raise NotImplementedError("PaperBroker.cancel_order — Sprint 2")

    async def get_order(self, broker_order_id: str) -> BrokerOrderAck:
        raise NotImplementedError("PaperBroker.get_order — Sprint 2")
