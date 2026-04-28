"""Questrade broker implementation.

Auth flow:
  1. Bootstrap: user pastes a manual refresh token into .env (QUESTRADE_REFRESH_TOKEN).
  2. First call to ensure_token() exchanges it for an access token + new refresh token.
     The api_server URL returned by Questrade is stored in the settings table.
  3. Every subsequent call checks token expiry and refreshes transparently.
  4. Refresh tokens are stored in the settings table so the .env value is only
     needed once.

Reference: https://www.questrade.com/api/documentation/getting-started
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.brokers.base import (
    BrokerAccount,
    BrokerBalance,
    BrokerInterface,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerPosition,
    BrokerQuote,
)
from app.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_URL = "{login_server}/oauth2/token"
_GRANT = "refresh_token"

# Seconds before expiry at which we proactively refresh.
_REFRESH_BUFFER_SECS = 60


class _TokenState:
    """In-process token cache. Replaced on each successful refresh."""

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        api_server: str,
        expires_at: datetime,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.api_server = api_server.rstrip("/") + "/"
        self.expires_at = expires_at

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(
            seconds=_REFRESH_BUFFER_SECS
        )


class QuestradeBroker(BrokerInterface):
    """Live Questrade broker. One instance per process; token state is shared."""

    name = "questrade"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._token: _TokenState | None = None
        self._lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def ensure_token(self) -> _TokenState:
        """Return a valid token, refreshing if needed. Thread-safe."""
        async with self._lock:
            if self._token is None or self._token.is_expired():
                await self._refresh()
        return self._token  # type: ignore[return-value]

    async def _refresh(self) -> None:
        """Exchange refresh token for a new access + refresh token pair."""
        refresh_token = await self._get_stored_refresh_token()
        if not refresh_token:
            raise RuntimeError(
                "No Questrade refresh token available. "
                "Set QUESTRADE_REFRESH_TOKEN in backend/.env and restart."
            )

        login_server = self._settings.questrade_login_server
        url = _TOKEN_URL.format(login_server=login_server)

        client = await self._client()
        resp = await client.post(
            url,
            params={
                "grant_type": _GRANT,
                "refresh_token": refresh_token,
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Questrade token refresh failed: {resp.status_code} {resp.text}"
            )

        data = resp.json()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])
        self._token = _TokenState(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            api_server=data["api_server"],
            expires_at=expires_at,
        )

        await self._persist_token(
            refresh_token=data["refresh_token"],
            api_server=data["api_server"],
        )
        log.info("Questrade token refreshed; api_server=%s", self._token.api_server)

    async def _get_stored_refresh_token(self) -> str | None:
        """Prefer DB-stored token; fall back to .env bootstrap token."""
        # Lazy import to avoid circular deps at module load time.
        from app.db.session import SessionLocal
        from app.services.settings_service import get_setting

        try:
            async with SessionLocal() as session:
                stored = await get_setting(session, "questrade_refresh_token")
                if stored:
                    return stored
        except Exception:
            pass  # DB not ready yet (e.g. first boot before migration)

        return self._settings.questrade_refresh_token or None

    async def _persist_token(self, refresh_token: str, api_server: str) -> None:
        """Write updated token + api_server back to the settings table."""
        from app.db.session import SessionLocal
        from app.services.settings_service import set_setting

        try:
            async with SessionLocal() as session:
                await set_setting(session, "questrade_refresh_token", refresh_token)
                await set_setting(session, "questrade_api_server", api_server)
                await session.commit()
        except Exception as exc:
            log.warning("Could not persist Questrade token to DB: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, **params: object) -> dict:
        token = await self.ensure_token()
        client = await self._client()
        url = token.api_server + path.lstrip("/")
        resp = await client.get(
            url,
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Questrade GET {path} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    async def _post(self, path: str, body: dict) -> dict:
        token = await self.ensure_token()
        client = await self._client()
        url = token.api_server + path.lstrip("/")
        resp = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Questrade POST {path} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    # ------------------------------------------------------------------
    # BrokerInterface — read
    # ------------------------------------------------------------------

    async def list_accounts(self) -> list[BrokerAccount]:
        data = await self._get("v1/accounts")
        return [
            BrokerAccount(
                broker_account_id=str(a["number"]),
                type=a.get("type", "Other"),
                primary_currency=a.get("primaryCurrency", "CAD"),
            )
            for a in data.get("accounts", [])
        ]

    async def get_balances(self, account_id: str) -> list[BrokerBalance]:
        data = await self._get(f"v1/accounts/{account_id}/balances")
        results: list[BrokerBalance] = []
        # Questrade returns perCurrencyBalances and combinedBalances
        for b in data.get("perCurrencyBalances", []):
            results.append(
                BrokerBalance(
                    account_id=account_id,
                    currency=b["currency"],
                    cash=Decimal(str(b.get("cash", 0))),
                    market_value=Decimal(str(b.get("marketValue", 0))),
                    total_equity=Decimal(str(b.get("totalEquity", 0))),
                    buying_power=Decimal(str(b.get("buyingPower", 0))),
                    maintenance_excess=Decimal(str(b.get("maintenanceExcess", 0)))
                    if b.get("maintenanceExcess") is not None
                    else None,
                )
            )
        return results

    async def get_positions(self, account_id: str) -> list[BrokerPosition]:
        data = await self._get(f"v1/accounts/{account_id}/positions")
        return [
            BrokerPosition(
                account_id=account_id,
                symbol=p["symbol"],
                currency=p.get("currency", "USD"),
                quantity=Decimal(str(p.get("openQuantity", 0))),
                avg_cost=Decimal(str(p.get("averageEntryPrice", 0))),
                current_price=Decimal(str(p["currentPrice"]))
                if p.get("currentPrice") is not None
                else None,
                market_value=Decimal(str(p.get("currentMarketValue", 0))),
                open_pnl=Decimal(str(p.get("openPnl", 0))),
            )
            for p in data.get("positions", [])
        ]

    # ------------------------------------------------------------------
    # BrokerInterface — quotes (Sprint 2)
    # ------------------------------------------------------------------

    async def get_quote(self, symbol: str) -> BrokerQuote:
        raise NotImplementedError("QuestradeBroker.get_quote — Sprint 2")

    def stream_quotes(self, symbols: list[str]) -> AsyncIterator[BrokerQuote]:
        raise NotImplementedError("QuestradeBroker.stream_quotes — Sprint 2")

    # ------------------------------------------------------------------
    # BrokerInterface — orders (Sprint 2)
    # ------------------------------------------------------------------

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderAck:
        raise NotImplementedError("QuestradeBroker.place_order — Sprint 2")

    async def cancel_order(self, broker_order_id: str) -> None:
        raise NotImplementedError("QuestradeBroker.cancel_order — Sprint 2")

    async def get_order(self, broker_order_id: str) -> BrokerOrderAck:
        raise NotImplementedError("QuestradeBroker.get_order — Sprint 2")
