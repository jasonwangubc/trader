"""Earnings calendar — syncs next earnings dates and EPS surprise history from yfinance.

We fetch for all screener symbols plus any currently held positions (tickets in
filled/triggered state). The data feeds:
  1. Ticket form warning when earnings are within 3 trading days of creation
  2. Screener card badge for PEP (Power Earnings Gap) candidates
  3. Positions page upcoming earnings column
  4. Monitor guard: flag tickets that might fire near earnings
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EarningsDate, ScreenerSymbol

log = logging.getLogger(__name__)

# How many trading days ahead to flag as "earnings soon"
EARNINGS_WARN_DAYS = 5


async def sync_earnings(
    session: AsyncSession,
    symbols: list[str] | None = None,
    max_concurrent: int = 5,
) -> dict[str, bool]:
    """Fetch earnings dates for the given symbols (or all active screener symbols).
    Returns {symbol: success}.
    """
    if symbols is None:
        result = await session.execute(
            select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
        )
        symbols = [r for (r,) in result.all()]

    if not symbols:
        return {}

    sem = asyncio.Semaphore(max_concurrent)
    results: dict[str, bool] = {}

    async def _fetch_one(sym: str) -> None:
        async with sem:
            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, _fetch_earnings_sync, sym)
                if data:
                    await _upsert(session, sym, data)
                    results[sym] = True
                else:
                    results[sym] = False
            except Exception:
                log.exception("Earnings fetch failed for %s", sym)
                results[sym] = False

    await asyncio.gather(*[_fetch_one(s) for s in symbols])
    await session.commit()
    log.info("Earnings sync complete: %d/%d succeeded", sum(results.values()), len(symbols))
    return results


def _fetch_earnings_sync(symbol: str) -> dict | None:
    """Synchronous yfinance call — run in executor to avoid blocking."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        # Next earnings
        cal = ticker.calendar
        next_date = None
        if cal is not None and not (hasattr(cal, "empty") and cal.empty):
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date")
                if raw:
                    next_date = raw[0] if isinstance(raw, list) else raw
            elif hasattr(cal, "loc"):
                try:
                    raw = cal.loc["Earnings Date"].iloc[0] if "Earnings Date" in cal.index else None
                    next_date = raw
                except Exception:
                    pass

        # Last EPS surprise
        last_surprise = None
        last_date = None
        hist = ticker.earnings_history
        if hist is not None and hasattr(hist, "empty") and not hist.empty:
            try:
                row = hist.sort_index().iloc[-1]
                reported = float(row.get("epsActual", 0) or 0)
                estimate = float(row.get("epsEstimate", 0) or 0)
                if estimate and estimate != 0:
                    last_surprise = (reported - estimate) / abs(estimate)
                last_date = hist.index[-1]
            except Exception:
                pass

        # Avg volume
        avg_vol = info.get("averageVolume") or info.get("averageDailyVolume10Day")

        return {
            "next_earnings_date": next_date,
            "last_eps_surprise_pct": last_surprise,
            "last_earnings_date": last_date,
            "avg_volume": avg_vol,
        }
    except Exception:
        return None


async def _upsert(session: AsyncSession, symbol: str, data: dict) -> None:
    existing = await session.execute(
        select(EarningsDate).where(EarningsDate.symbol == symbol)
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = EarningsDate(symbol=symbol)
        session.add(row)

    def _to_datetime(d) -> datetime | None:
        if d is None:
            return None
        if isinstance(d, datetime):
            return d.replace(tzinfo=None)
        try:
            import pandas as pd
            if hasattr(d, "to_pydatetime"):
                return d.to_pydatetime().replace(tzinfo=None)
            return pd.Timestamp(d).to_pydatetime().replace(tzinfo=None)
        except Exception:
            return None

    row.next_earnings_date = _to_datetime(data.get("next_earnings_date"))
    row.last_earnings_date = _to_datetime(data.get("last_earnings_date"))
    row.last_eps_surprise_pct = (
        Decimal(str(round(data["last_eps_surprise_pct"], 4)))
        if data.get("last_eps_surprise_pct") is not None else None
    )
    row.avg_volume = int(data["avg_volume"]) if data.get("avg_volume") else None
    row.synced_at = datetime.now(timezone.utc)


async def get_earnings_map(
    session: AsyncSession, symbols: list[str]
) -> dict[str, EarningsDate]:
    """Return {symbol: EarningsDate} for the given symbols."""
    result = await session.execute(
        select(EarningsDate).where(EarningsDate.symbol.in_(symbols))
    )
    return {r.symbol: r for r in result.scalars().all()}


def days_to_earnings(earnings_row: EarningsDate | None) -> int | None:
    """Calendar days until next earnings (None if unknown)."""
    if earnings_row is None or earnings_row.next_earnings_date is None:
        return None
    delta = earnings_row.next_earnings_date - datetime.now()
    return delta.days


def earnings_warning(earnings_row: EarningsDate | None) -> str | None:
    """Return a warning string if earnings are imminent, else None."""
    days = days_to_earnings(earnings_row)
    if days is None:
        return None
    if days < 0:
        return None   # already passed
    if days <= 1:
        return f"Earnings TOMORROW — do not enter or hold through unless you have an EP thesis."
    if days <= EARNINGS_WARN_DAYS:
        return f"Earnings in {days} days — consider waiting for the report before entering."
    return None


def is_pep_candidate(
    earnings_row: EarningsDate | None,
    bars_today_volume: int | None,
    price_gap_pct: float | None,
) -> bool:
    """Power Earnings Gap: stock gapped up ≥5% on ≥2× average volume on/after earnings."""
    if earnings_row is None:
        return False
    if price_gap_pct is None or price_gap_pct < 0.05:
        return False
    avg_vol = earnings_row.avg_volume or 0
    if avg_vol > 0 and bars_today_volume:
        return bars_today_volume >= avg_vol * 2
    return False
