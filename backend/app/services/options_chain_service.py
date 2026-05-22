"""Options-chain fetcher.

Source: yfinance (free, no API key, ~5-15 min delayed).
Used by the wheel-strategy scanner and the wheel candidate detail view.

The chain for a single symbol is ~50-200 contracts per expiry across 8-15
expiries. yfinance returns one expiry per HTTP call, so we deliberately
fetch only the expiries close to the requested DTE band rather than the
whole surface.

Cached in-process with a short TTL (5 min) because a wheel scan often
hits the same handful of expiries across many candidates.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)


# Module-level TTL cache. Keyed by (symbol, expiry-iso).
_CHAIN_CACHE: dict[tuple[str, str], tuple[float, "OptionChain"]] = {}
_EXPIRIES_CACHE: dict[str, tuple[float, list[str]]] = {}
_TTL_SECONDS = 300  # 5 minutes


@dataclass
class OptionQuote:
    """One option contract quote, normalized."""
    symbol: str                # underlying
    expiry: date
    strike: Decimal
    option_type: str           # "put" | "call"
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    mid: Decimal               # (bid+ask)/2, fall back to last
    open_interest: int
    volume: int
    implied_volatility: Decimal | None


@dataclass
class OptionChain:
    symbol: str
    expiry: date
    puts: list[OptionQuote]
    calls: list[OptionQuote]


def _now() -> float:
    return time.time()


def _cache_get_expiries(symbol: str) -> list[str] | None:
    entry = _EXPIRIES_CACHE.get(symbol)
    if entry is None:
        return None
    ts, value = entry
    if _now() - ts > _TTL_SECONDS:
        return None
    return value


def _cache_set_expiries(symbol: str, value: list[str]) -> None:
    _EXPIRIES_CACHE[symbol] = (_now(), value)


def _cache_get_chain(symbol: str, expiry_iso: str) -> OptionChain | None:
    entry = _CHAIN_CACHE.get((symbol, expiry_iso))
    if entry is None:
        return None
    ts, value = entry
    if _now() - ts > _TTL_SECONDS:
        return None
    return value


def _cache_set_chain(symbol: str, expiry_iso: str, value: OptionChain) -> None:
    _CHAIN_CACHE[(symbol, expiry_iso)] = (_now(), value)


def _to_decimal(x) -> Decimal | None:
    if x is None:
        return None
    try:
        f = float(x)
        if f != f:  # NaN
            return None
        return Decimal(str(round(f, 6)))
    except (TypeError, ValueError):
        return None


def _df_to_quotes(df: pd.DataFrame, symbol: str, expiry: date, option_type: str) -> list[OptionQuote]:
    if df is None or df.empty:
        return []
    out: list[OptionQuote] = []
    for _, row in df.iterrows():
        bid = _to_decimal(row.get("bid"))
        ask = _to_decimal(row.get("ask"))
        last = _to_decimal(row.get("lastPrice"))
        # mid: prefer (bid+ask)/2 if both sides quoted, else last, else best of bid/ask
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
        elif last is not None and last > 0:
            mid = last
        elif ask is not None and ask > 0:
            mid = ask
        elif bid is not None and bid > 0:
            mid = bid
        else:
            continue  # no usable price
        strike = _to_decimal(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        oi = row.get("openInterest")
        vol = row.get("volume")
        out.append(
            OptionQuote(
                symbol=symbol,
                expiry=expiry,
                strike=strike,
                option_type=option_type,
                bid=bid,
                ask=ask,
                last=last,
                mid=mid,
                open_interest=int(oi) if pd.notna(oi) else 0,
                volume=int(vol) if pd.notna(vol) else 0,
                implied_volatility=_to_decimal(row.get("impliedVolatility")),
            )
        )
    return out


async def list_expiries(symbol: str) -> list[date]:
    """Return all listed expiries for `symbol`, soonest first."""
    cached = _cache_get_expiries(symbol)
    if cached is None:
        loop = asyncio.get_event_loop()
        try:
            expiries = await loop.run_in_executor(None, lambda: list(yf.Ticker(symbol).options))
        except Exception:
            log.warning("yfinance: failed to fetch expiries for %s", symbol, exc_info=False)
            expiries = []
        _cache_set_expiries(symbol, expiries)
        cached = expiries
    out: list[date] = []
    for e in cached:
        try:
            out.append(date.fromisoformat(e))
        except ValueError:
            continue
    return out


async def fetch_chain(symbol: str, expiry: date) -> OptionChain | None:
    """Fetch puts + calls for one (symbol, expiry). Cached for 5 min."""
    expiry_iso = expiry.isoformat()
    cached = _cache_get_chain(symbol, expiry_iso)
    if cached is not None:
        return cached

    loop = asyncio.get_event_loop()
    try:
        raw = await loop.run_in_executor(
            None, lambda: yf.Ticker(symbol).option_chain(expiry_iso)
        )
    except Exception:
        log.warning("yfinance: failed to fetch chain %s @ %s", symbol, expiry_iso, exc_info=False)
        return None

    chain = OptionChain(
        symbol=symbol,
        expiry=expiry,
        puts=_df_to_quotes(raw.puts, symbol, expiry, "put"),
        calls=_df_to_quotes(raw.calls, symbol, expiry, "call"),
    )
    _cache_set_chain(symbol, expiry_iso, chain)
    return chain


def pick_expiry_near_dte(expiries: list[date], target_dte: int, tolerance: int = 10) -> date | None:
    """Pick the expiry whose DTE is closest to `target_dte`, within `tolerance` days.

    Returns None if no expiry is within tolerance — caller should skip the symbol
    rather than trade a wildly-different expiry.
    """
    if not expiries:
        return None
    today = datetime.now(timezone.utc).date()
    best: tuple[int, date] | None = None
    for e in expiries:
        dte = (e - today).days
        if dte <= 0:
            continue
        diff = abs(dte - target_dte)
        if best is None or diff < best[0]:
            best = (diff, e)
    if best is None or best[0] > tolerance:
        return None
    return best[1]


def days_to_expiry(expiry: date) -> int:
    return (expiry - datetime.now(timezone.utc).date()).days
