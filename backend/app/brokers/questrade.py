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
import hashlib
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from app.brokers.base import (
    BrokerAccount,
    BrokerBalance,
    BrokerBracketAck,
    BrokerBracketRequest,
    BrokerExecution,
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

    async def place_bracket_order(self, req: BrokerBracketRequest) -> BrokerBracketAck:
        """Submit an atomic bracket: stop-limit Buy + stop-market Sell GTC.

        Hits POST /v1/accounts/{accountId}/orders/bracket. Questrade holds the
        StopLoss child dormant until the Primary fills, then activates it
        broker-side. If we die between submit and fill, the bracket is still
        intact at Questrade — no naked window.
        """
        sym_to_id = await self._resolve_symbol_ids([req.symbol])
        symbol_id = sym_to_id.get(req.symbol)
        if not symbol_id:
            raise RuntimeError(f"Cannot resolve Questrade symbolId for {req.symbol}")

        body: dict = {
            "accountId": req.account_id,
            "symbolId": symbol_id,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
            "components": [
                {
                    "orderClass": "Primary",
                    "orderType": "StopLimit",
                    "action": "Buy",
                    "quantity": req.quantity,
                    "stopPrice": float(req.entry_stop_price),
                    "limitPrice": float(req.entry_limit_price),
                    "timeInForce": req.entry_time_in_force,
                    "isAllOrNone": False,
                    "isAnonymous": False,
                },
                {
                    "orderClass": "StopLoss",
                    "orderType": "StopMarket",
                    "action": "Sell",
                    "quantity": req.quantity,
                    "stopPrice": float(req.stop_loss_price),
                    "timeInForce": req.stop_loss_time_in_force,
                    "isAllOrNone": False,
                    "isAnonymous": False,
                },
            ],
        }

        data = await self._post(f"v1/accounts/{req.account_id}/orders/bracket", body)
        orders = data.get("orders") or []
        if len(orders) < 2:
            raise RuntimeError(
                f"Questrade bracket returned {len(orders)} orders; expected 2. Body: {data}"
            )

        primary_o = next((o for o in orders if o.get("orderClass") == "Primary"), None)
        stop_o = next((o for o in orders if o.get("orderClass") == "StopLoss"), None)
        if primary_o is None or stop_o is None:
            raise RuntimeError(
                f"Questrade bracket response missing Primary/StopLoss components: {data}"
            )

        now = datetime.now(timezone.utc)
        primary_ack = BrokerOrderAck(
            broker_order_id=str(primary_o["id"]),
            status=self._map_qt_state(primary_o.get("state", "")),
            submitted_at=now,
        )
        stop_ack = BrokerOrderAck(
            broker_order_id=str(stop_o["id"]),
            status=self._map_qt_state(stop_o.get("state", "")),
            submitted_at=now,
        )
        return BrokerBracketAck(primary=primary_ack, stop_loss=stop_ack)

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

    async def get_executions(
        self,
        account_id: str,
        start: datetime,
        end: datetime,
    ) -> list[BrokerExecution]:
        """Pull every fill in [start, end] for this account.

        ⚠️ Despite what `startTime`/`endTime` suggest, Questrade's /executions
        endpoint only retains roughly the last 30 days. For historical backfills,
        use `get_activities` instead — that endpoint goes back years.

        We keep this around for near-real-time intraday use (per-fill timestamps
        and exchange execution IDs that activities don't have).

        The Questrade payload uses ISO-8601 with a -05:00 / -04:00 offset
        depending on DST; we normalize to UTC.
        """
        params = {
            "startTime": start.astimezone(timezone.utc).isoformat(),
            "endTime":   end.astimezone(timezone.utc).isoformat(),
        }
        data = await self._get(f"v1/accounts/{account_id}/executions", **params)
        out: list[BrokerExecution] = []
        for ex in data.get("executions", []):
            ts_str = ex.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts_utc = ts.astimezone(timezone.utc)
            except Exception:
                continue
            symbol = ex.get("symbol") or ""
            qty_raw = ex.get("quantity")
            price_raw = ex.get("price")
            if qty_raw is None or price_raw is None or not symbol:
                continue
            # Total fees = explicit commission + execution + sec + canadian fees + placement
            commission = (
                self._dec(ex.get("commission")) +
                self._dec(ex.get("executionFee")) +
                self._dec(ex.get("secFee")) +
                self._dec(ex.get("canadianExecutionFee")) +
                self._dec(ex.get("orderPlacementCommission"))
            )
            out.append(BrokerExecution(
                broker_execution_id=str(ex.get("id", "")),
                broker_order_id=str(ex.get("orderId", "")) if ex.get("orderId") else None,
                account_id=account_id,
                symbol=symbol,
                currency=_infer_currency(symbol),
                side=(ex.get("side") or "").lower(),  # "buy" | "sell"
                quantity=Decimal(str(qty_raw)),
                price=Decimal(str(price_raw)),
                commission=commission,
                executed_at=ts_utc,
                venue=ex.get("venue"),
                raw=ex,
            ))
        return out

    async def get_activities(
        self,
        account_id: str,
        start: datetime,
        end: datetime,
    ) -> list[BrokerExecution]:
        """Pull Trade-type activities in [start, end] for this account.

        Unlike /executions (which has ~30-day retention regardless of
        startTime), /activities goes back to account opening. This is the
        canonical historical source.

        Trade-offs vs /executions:
          • Date precision only (Questrade returns tradeDate at midnight,
            no intra-day time). FIFO matching only needs chronological order
            within a day so this is fine for the journal use case.
          • No stable execution id — we synthesize one by SHA-1 hashing the
            trade fields. Collision risk is negligible in practice (two
            identical-priced fills on the same calendar day for the same
            symbol with identical descriptions).

        Activities also include Dividends, Deposits, FX, etc. — we filter
        to type='Trades'. Quantities in activities are sometimes signed
        (negative for sells); we take abs() and trust `action` for side.
        Commissions arrive negative (cost); we store as positive.
        """
        params = {
            "startTime": start.astimezone(timezone.utc).isoformat(),
            "endTime":   end.astimezone(timezone.utc).isoformat(),
        }
        data = await self._get(f"v1/accounts/{account_id}/activities", **params)
        out: list[BrokerExecution] = []
        for a in data.get("activities", []):
            if a.get("type") != "Trades":
                continue
            ts_str = a.get("tradeDate")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts_utc = ts.astimezone(timezone.utc)
            except Exception:
                continue

            symbol = a.get("symbol") or ""
            if not symbol:
                continue
            action = (a.get("action") or "").lower()
            if action not in ("buy", "sell"):
                continue

            qty_raw = a.get("quantity")
            price_raw = a.get("price")
            if qty_raw is None or price_raw is None:
                continue
            try:
                qty = abs(Decimal(str(qty_raw)))
                price = Decimal(str(price_raw))
            except Exception:
                continue
            if qty <= 0 or price <= 0:
                continue

            commission = abs(self._dec(a.get("commission")))

            # Synthetic dedup id — stable across re-syncs of the same window.
            key_input = (
                f"{account_id}|{ts_utc.isoformat()}|{symbol}|{action}|"
                f"{qty}|{price}|{a.get('grossAmount', '')}|"
                f"{(a.get('description') or '')[:60]}"
            )
            synthetic_id = "act_" + hashlib.sha1(key_input.encode()).hexdigest()[:40]

            out.append(BrokerExecution(
                broker_execution_id=synthetic_id,
                broker_order_id=None,   # activities don't carry orderId
                account_id=account_id,
                symbol=symbol,
                currency=a.get("currency") or _infer_currency(symbol),
                side=action,
                quantity=qty,
                price=price,
                commission=commission,
                executed_at=ts_utc,
                venue=None,
                raw=a,
            ))
        return out
