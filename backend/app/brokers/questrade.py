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
    BrokerOpenOrder,
    BrokerOrderAck,
    BrokerOrderRequest,
    BrokerPosition,
    BrokerQuote,
)
from app.config import get_settings

log = logging.getLogger(__name__)

_TOKEN_URL = "{login_server}/oauth2/token"
_GRANT = "refresh_token"

_REFRESH_BUFFER_SECS = 60

_RECOVERY_HINT = (
    "Generate a new token at https://login.questrade.com/APIAccess/UserApps.aspx "
    "and paste it on the Settings page (or set QUESTRADE_REFRESH_TOKEN in backend/.env)."
)


def _format_refresh_error(resp: httpx.Response) -> str:
    """Turn a Questrade non-200 token-refresh response into a clean one-line message.

    Questrade frequently returns HTML error pages (especially on 5xx) and
    bare 400/401 with JSON. We avoid leaking HTML into the UI and instead
    classify by status code so the user knows whether to retry or re-paste.
    """
    code = resp.status_code
    body = (resp.text or "").strip()
    looks_html = body.lower().startswith(("<!doctype", "<html"))

    if 500 <= code < 600:
        return (
            f"Questrade auth server returned {code} (transient). "
            "Retry in a minute. If it persists, re-paste a fresh token. "
            f"{_RECOVERY_HINT}"
        )
    if code in (400, 401, 403):
        return f"Questrade rejected the refresh token ({code}). {_RECOVERY_HINT}"

    # Unknown — include status but never the HTML body
    snippet = "<error page>" if looks_html else (body[:120] if body else "no body")
    return f"Questrade token refresh failed: {code} ({snippet}). {_RECOVERY_HINT}"


def _infer_currency(symbol: str) -> str:
    """Infer trading currency from the symbol suffix.

    Questrade's positions API does not include a currency field, so we derive
    it from the exchange suffix. The .U.TO / .U.V form denotes a USD-denominated
    fund listed on a Canadian exchange (e.g. PSU.U.TO). Plain .TO / .V / .VN
    trade in CAD. Everything else is assumed USD (US-listed).
    """
    s = symbol.upper()
    if s.endswith(".U.TO") or s.endswith(".U.V") or s.endswith(".U.VN"):
        return "USD"
    if s.endswith(".TO") or s.endswith(".V") or s.endswith(".VN"):
        return "CAD"
    return "USD"


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
    """Live Questrade broker. One instance per user; token state is per-user."""

    name = "questrade"

    def __init__(self, user_id: str = "user_default") -> None:
        self._user_id = user_id
        self._settings = get_settings()
        self._token: _TokenState | None = None
        self._lock = asyncio.Lock()
        self._http: httpx.AsyncClient | None = None
        self._symbol_id_cache: dict[str, int] = {}

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

    async def _refresh(self, *, env_fallback: bool = False) -> None:
        """Exchange refresh token for a new access + refresh token pair.

        env_fallback=True means the DB token already failed — try .env instead.
        """
        refresh_token = await self._get_stored_refresh_token(env_fallback=env_fallback)
        if not refresh_token:
            raise RuntimeError(
                "No Questrade refresh token available. "
                "Generate a new token at https://login.questrade.com/APIAccess/UserApps.aspx "
                "and set QUESTRADE_REFRESH_TOKEN in backend/.env, then restart the backend."
            )

        login_server = self._settings.questrade_login_server
        url = _TOKEN_URL.format(login_server=login_server)

        client = await self._client()
        resp = await client.post(
            url,
            params={"grant_type": _GRANT, "refresh_token": refresh_token},
        )

        if resp.status_code == 400 and not env_fallback:
            # DB token is stale (already rotated or expired). Try .env bootstrap.
            log.warning("DB refresh token rejected (400); retrying with .env token")
            return await self._refresh(env_fallback=True)

        if resp.status_code != 200:
            raise RuntimeError(_format_refresh_error(resp))

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

    def _token_key(self) -> str:
        """DB settings key for this user's Questrade refresh token."""
        uid = self._user_id
        if uid == "user_default":
            return "questrade_refresh_token"
        return f"{uid}:questrade_refresh_token"

    def _api_server_key(self) -> str:
        uid = self._user_id
        if uid == "user_default":
            return "questrade_api_server"
        return f"{uid}:questrade_api_server"

    async def _get_stored_refresh_token(self, *, env_fallback: bool = False) -> str | None:
        """Return the best available refresh token.

        Normal flow: prefer the DB-persisted token (which is the most recently
        rotated one) over the .env bootstrap token.

        env_fallback=True: skip the DB token and go straight to .env. Used
        when the DB token just returned a 401 (already rotated or revoked).
        """
        from app.db.session import SessionLocal
        from app.services.settings_service import del_setting, get_setting

        env_token = self._settings.questrade_refresh_token or None

        if env_fallback:
            if env_token:
                log.info("Falling back to .env QUESTRADE_REFRESH_TOKEN after DB token failure")
                # Clear the stale DB token so the next boot doesn't try it again.
                try:
                    async with SessionLocal() as session:
                        await del_setting(session, "questrade_refresh_token")
                        await session.commit()
                except Exception:
                    pass
            return env_token

        try:
            async with SessionLocal() as session:
                stored = await get_setting(session, self._token_key())
                if stored:
                    return stored
        except Exception:
            pass  # DB not ready yet (e.g. first boot before migration)

        # Only fall back to the .env token for the default (single-user dev) user.
        # Real Clerk users must connect via Settings — the global env token is not theirs.
        if self._user_id == "user_default":
            return env_token
        return None

    async def _persist_token(self, refresh_token: str, api_server: str) -> None:
        """Write updated token + api_server back to the settings table (per user)."""
        from app.db.session import SessionLocal
        from app.services.settings_service import set_setting

        try:
            async with SessionLocal() as session:
                await set_setting(session, self._token_key(), refresh_token)
                await set_setting(session, self._api_server_key(), api_server)
                await session.commit()
        except Exception as exc:
            log.warning("Could not persist Questrade token to DB: %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, *, _retry: bool = True, **params: object) -> dict:
        token = await self.ensure_token()
        client = await self._client()
        url = token.api_server + path.lstrip("/")
        resp = await client.get(
            url,
            params={k: v for k, v in params.items() if v is not None},
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        if resp.status_code == 401 and _retry:
            # Access token was invalidated mid-flight. Force a refresh and retry once.
            log.warning("Questrade GET %s returned 401; forcing token refresh", path)
            async with self._lock:
                self._token = None
                await self._refresh()
            return await self._get(path, _retry=False, **params)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Questrade GET {path} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    async def _delete(self, path: str, *, _retry: bool = True) -> None:
        token = await self.ensure_token()
        client = await self._client()
        url = token.api_server + path.lstrip("/")
        resp = await client.delete(url, headers={"Authorization": f"Bearer {token.access_token}"})
        if resp.status_code == 401 and _retry:
            async with self._lock:
                self._token = None
                await self._refresh()
            return await self._delete(path, _retry=False)
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Questrade DELETE {path} failed: {resp.status_code} {resp.text}")

    async def _post(self, path: str, body: dict, *, _retry: bool = True) -> dict:
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
        if resp.status_code == 401 and _retry:
            log.warning("Questrade POST %s returned 401; forcing token refresh", path)
            async with self._lock:
                self._token = None
                await self._refresh()
            return await self._post(path, body, _retry=False)
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

    @staticmethod
    def _dec(value, default=0) -> Decimal:
        """Safely convert a Questrade numeric field to Decimal.
        Questrade sometimes returns None for fields like averageEntryPrice
        on positions with no cost basis (e.g. options, fractional shares).
        """
        v = value if value is not None else default
        try:
            return Decimal(str(v))
        except Exception:
            return Decimal(str(default))

    async def get_positions(self, account_id: str) -> list[BrokerPosition]:
        data = await self._get(f"v1/accounts/{account_id}/positions")
        return [
            BrokerPosition(
                account_id=account_id,
                symbol=p["symbol"],
                currency=p.get("currency") or _infer_currency(p["symbol"]),
                quantity=self._dec(p.get("openQuantity")),
                avg_cost=self._dec(p.get("averageEntryPrice")),
                current_price=self._dec(p["currentPrice"])
                if p.get("currentPrice") is not None
                else None,
                market_value=self._dec(p.get("currentMarketValue")),
                open_pnl=self._dec(p.get("openPnl")),
            )
            for p in data.get("positions", [])
        ]

    # ------------------------------------------------------------------
    # BrokerInterface — quotes
    # ------------------------------------------------------------------

    async def _resolve_symbol_ids(self, symbols: list[str]) -> dict[str, int]:
        """Return {symbol: symbolId} for the given list, using cache where possible."""
        missing = [s for s in symbols if s not in self._symbol_id_cache]
        if missing:
            # Questrade accepts comma-separated symbol names.
            data = await self._get("v1/symbols", names=",".join(missing))
            for info in data.get("symbols", []):
                name = info.get("symbol") or info.get("symbolCode")
                sid = info.get("symbolId")
                if name and sid:
                    self._symbol_id_cache[name] = sid
                    # Also cache without .TO/.V suffix → same ID so bare lookups work.
                    bare = name.split(".")[0]
                    if bare not in self._symbol_id_cache:
                        self._symbol_id_cache[bare] = sid
        return {s: self._symbol_id_cache[s] for s in symbols if s in self._symbol_id_cache}

    async def get_quote(self, symbol: str) -> BrokerQuote:
        quotes = await self.get_quotes_batch([symbol])
        if symbol not in quotes:
            raise RuntimeError(f"No quote returned for {symbol}")
        return quotes[symbol]

    async def get_quotes_batch(self, symbols: list[str]) -> dict[str, BrokerQuote]:
        """Fetch quotes for multiple symbols in one Questrade API call."""
        sym_to_id = await self._resolve_symbol_ids(symbols)
        if not sym_to_id:
            return {}
        ids_param = ",".join(str(v) for v in sym_to_id.values())
        data = await self._get("v1/markets/quotes", ids=ids_param)

        id_to_sym = {v: k for k, v in sym_to_id.items()}
        out: dict[str, BrokerQuote] = {}
        now = datetime.now(timezone.utc)

        for q in data.get("quotes", []):
            sid = q.get("symbolId")
            sym = id_to_sym.get(sid, q.get("symbol", ""))
            last_raw = q.get("lastTradePriceTrHrs") or q.get("lastTradePrice")
            if last_raw is None:
                continue
            out[sym] = BrokerQuote(
                symbol=sym,
                last=Decimal(str(last_raw)),
                bid=Decimal(str(q["bidPrice"])) if q.get("bidPrice") is not None else None,
                ask=Decimal(str(q["askPrice"])) if q.get("askPrice") is not None else None,
                volume=int(q["volume"]) if q.get("volume") is not None else None,
                at=now,
                delay=int(q.get("delay", 0)),
            )
        return out

    def stream_quotes(self, symbols: list[str]) -> AsyncIterator[BrokerQuote]:
        raise NotImplementedError("QuestradeBroker.stream_quotes — Sprint 3")

    # ------------------------------------------------------------------
    # BrokerInterface — orders
    # ------------------------------------------------------------------

    _ORDER_TYPE_MAP = {
        "market":     "Market",
        "limit":      "Limit",
        "stop_market":"StopMarket",
        "stop_limit": "StopLimit",
    }
    _QT_STATE_MAP = {
        "Queued": "pending",
        "Accepted": "accepted",
        "Rejected": "rejected",
        "Canceled": "cancelled",
        "Execution": "partial",
        "FilledAll": "filled",
        "FilledPartially": "partial",
        "ReplacePending": "pending",
        "Replaced": "cancelled",
        "StopTriggered": "accepted",
    }

    def _map_qt_state(self, state: str) -> str:
        return self._QT_STATE_MAP.get(state, "pending")

    async def place_order(self, req: BrokerOrderRequest) -> BrokerOrderAck:
        sym_to_id = await self._resolve_symbol_ids([req.symbol])
        symbol_id = sym_to_id.get(req.symbol)
        if not symbol_id:
            raise RuntimeError(f"Cannot resolve Questrade symbolId for {req.symbol}")

        body: dict = {
            "accountId": req.account_id,
            "symbolId": symbol_id,
            "quantity": req.quantity,
            "orderType": self._ORDER_TYPE_MAP[req.order_type],
            "action": req.side.capitalize(),
            "timeInForce": req.time_in_force,
            "isAllOrNone": False,
            "isAnonymous": False,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
        }
        if req.limit_price is not None:
            body["limitPrice"] = float(req.limit_price)
        if req.stop_price is not None:
            body["stopPrice"] = float(req.stop_price)

        data = await self._post(f"v1/accounts/{req.account_id}/orders", body)
        orders = data.get("orders", []) or [data]
        order = orders[0]

        return BrokerOrderAck(
            broker_order_id=str(order["id"]),
            status=self._map_qt_state(order.get("state", "")),
            submitted_at=datetime.now(timezone.utc),
        )

    async def get_order(self, account_id: str, broker_order_id: str) -> BrokerOrderAck:
        data = await self._get(f"v1/accounts/{account_id}/orders/{broker_order_id}")
        orders = data.get("orders", []) or [data]
        order = orders[0]

        fill_price = None
        fill_qty = None
        if order.get("filledQuantity") and order.get("avgExecPrice"):
            fill_price = Decimal(str(order["avgExecPrice"]))
            fill_qty = int(order["filledQuantity"])

        return BrokerOrderAck(
            broker_order_id=broker_order_id,
            status=self._map_qt_state(order.get("state", "")),
            submitted_at=datetime.now(timezone.utc),
            fill_price=fill_price,
            fill_quantity=fill_qty,
        )

    async def cancel_order(self, account_id: str, broker_order_id: str) -> None:
        await self._delete(f"v1/accounts/{account_id}/orders/{broker_order_id}")

    _ORDER_TYPE_REVERSE = {
        "Market":     "market",
        "Limit":      "limit",
        "StopMarket": "stop_market",
        "StopLimit":  "stop_limit",
    }

    async def get_open_orders(self, account_id: str) -> list[BrokerOpenOrder]:
        """List currently-pending orders for this Questrade account.

        Uses Questrade's `stateFilter=Open` which covers Queued / Accepted /
        StopTriggered / partially-filled — i.e. anything still working at the
        exchange. Excludes filled, cancelled, expired, and rejected orders.
        """
        data = await self._get(f"v1/accounts/{account_id}/orders", stateFilter="Open")
        out: list[BrokerOpenOrder] = []
        for o in data.get("orders", []):
            total_qty  = int(o.get("totalQuantity") or 0)
            filled_qty = int(o.get("filledQuantity") or 0)
            open_qty   = max(0, total_qty - filled_qty)
            if open_qty == 0:
                continue
            symbol = o.get("symbol") or ""
            limit_p = o.get("limitPrice")
            stop_p  = o.get("stopPrice")
            submitted_str = o.get("creationTime")
            submitted_at: datetime | None = None
            if submitted_str:
                try:
                    submitted_at = datetime.fromisoformat(submitted_str.replace("Z", "+00:00"))
                except Exception:
                    submitted_at = None
            out.append(BrokerOpenOrder(
                broker_order_id=str(o.get("id", "")),
                account_id=account_id,
                symbol=symbol,
                currency=_infer_currency(symbol),
                side=(o.get("side") or "").lower(),
                order_type=self._ORDER_TYPE_REVERSE.get(o.get("orderType", ""), "limit"),
                quantity=open_qty,
                limit_price=Decimal(str(limit_p)) if limit_p is not None else None,
                stop_price=Decimal(str(stop_p))   if stop_p   is not None else None,
                submitted_at=submitted_at,
                status=self._map_qt_state(o.get("state", "")),
            ))
        return out
