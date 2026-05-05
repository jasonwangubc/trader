"""End-of-day data pipeline using yfinance.

Downloads adjusted OHLCV for all active screener symbols plus benchmark tickers
(SPY for US, XIU.TO for TSX). Stores in daily_bars. Safe to run repeatedly — uses
upsert logic so re-runs are idempotent.

Lookback: 2 years (504 trading days) to support 200-day MA and 52-week stats.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyBar, ScreenerSymbol
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

BENCHMARKS = ["SPY", "XIU.TO"]   # RS comparison universe
LOOKBACK_YEARS = 2


async def sync_eod_incremental(
    session: AsyncSession,
    symbols: list[str],
    *,
    full_years: int = 2,
    delta_days: int = 35,
) -> dict[str, int]:
    """Incremental download: symbols with existing bars get `delta_days` of new data;
    new symbols get `full_years` of history. Much faster on subsequent runs."""
    from sqlalchemy import func, text

    # Find the latest bar date per symbol in one query.
    result = await session.execute(
        text(
            "SELECT symbol, MAX(bar_date) AS latest "
            "FROM daily_bars WHERE symbol = ANY(:syms) GROUP BY symbol"
        ),
        {"syms": list(symbols)},
    )
    latest_by_sym = {row.symbol: row.latest for row in result}

    cutoff_full = (datetime.now(timezone.utc) - timedelta(days=full_years * 365 + 30)).strftime("%Y-%m-%d")
    cutoff_delta = (datetime.now(timezone.utc) - timedelta(days=delta_days)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    new_symbols = [s for s in symbols if s not in latest_by_sym]
    existing_symbols = [s for s in symbols if s in latest_by_sym]

    counts: dict[str, int] = {}

    # New symbols: full history download.
    if new_symbols:
        log.info("Full download for %d new symbols (start=%s)", len(new_symbols), cutoff_full)
        counts.update(await _bulk_download_and_store(session, new_symbols, cutoff_full, today))

    # Existing: only delta.
    if existing_symbols:
        log.info("Delta download for %d existing symbols (last %d days)", len(existing_symbols), delta_days)
        counts.update(await _bulk_download_and_store(session, existing_symbols, cutoff_delta, today))

    await session.commit()
    return counts


async def _bulk_download_and_store(
    session: AsyncSession,
    symbols: list[str],
    start: str,
    end: str,
) -> dict[str, int]:
    """Download a batch of symbols from yfinance and upsert into daily_bars."""
    if not symbols:
        return {}
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(
            None,
            lambda: yf.download(
                symbols,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                threads=True,
            ),
        )
    except Exception:
        log.exception("yfinance bulk download failed for %d symbols", len(symbols))
        return {s: 0 for s in symbols}

    counts: dict[str, int] = {}
    for sym in symbols:
        try:
            counts[sym] = await _upsert_symbol_bars(session, sym, raw, symbols)
        except Exception:
            log.exception("Failed to upsert bars for %s", sym)
            counts[sym] = 0
    return counts


async def sync_eod_data(
    session: AsyncSession,
    symbols: list[str] | None = None,
) -> dict[str, int]:
    """Download bars for given symbols (or all active watchlist + benchmarks).
    Returns {symbol: bars_upserted}.
    """
    if symbols is None:
        result = await session.execute(
            select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
        )
        symbols = [r for (r,) in result.all()]

    all_symbols = list(set(symbols) | set(BENCHMARKS))
    if not all_symbols:
        return {}

    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_YEARS * 365 + 30)).strftime("%Y-%m-%d")
    log.info("Downloading %d symbols from %s", len(all_symbols), start)

    try:
        raw = yf.download(
            all_symbols,
            start=start,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception:
        log.exception("yfinance download failed")
        return {}

    counts: dict[str, int] = {}
    for sym in all_symbols:
        try:
            counts[sym] = await _upsert_symbol_bars(session, sym, raw, all_symbols)
        except Exception:
            log.exception("Failed to upsert bars for %s", sym)
            counts[sym] = 0

    await session.commit()
    log.info("EOD sync complete: %s", counts)
    return counts


async def _upsert_symbol_bars(
    session: AsyncSession,
    symbol: str,
    raw: pd.DataFrame,
    all_symbols: list[str],
) -> int:
    """Extract one symbol from the yfinance multi-symbol DataFrame and upsert."""
    try:
        if len(all_symbols) == 1:
            df = raw.copy()
        else:
            # Multi-symbol download uses a MultiIndex: (field, symbol)
            df = raw.xs(symbol, axis=1, level=1).copy() if symbol in raw.columns.get_level_values(1) else pd.DataFrame()
    except Exception:
        log.warning("No data in download for %s", symbol)
        return 0

    if df.empty or len(df) < 2:
        log.warning("Insufficient data for %s (%d rows)", symbol, len(df))
        return 0

    df = df.dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index)

    upserted = 0
    for bar_date, row in df.iterrows():
        try:
            dt = bar_date.to_pydatetime().replace(tzinfo=None)
            existing = await session.execute(
                select(DailyBar).where(
                    DailyBar.symbol == symbol,
                    DailyBar.bar_date == dt,
                )
            )
            bar = existing.scalar_one_or_none()
            if bar is None:
                bar = DailyBar(symbol=symbol, bar_date=dt)
                session.add(bar)

            bar.open = Decimal(str(round(float(row.get("Open", row["Close"])), 6)))
            bar.high = Decimal(str(round(float(row.get("High", row["Close"])), 6)))
            bar.low = Decimal(str(round(float(row.get("Low", row["Close"])), 6)))
            bar.close = Decimal(str(round(float(row["Close"]), 6)))
            bar.volume = int(row.get("Volume", 0))
            bar.adj_close = bar.close  # auto_adjust=True means Close IS already adjusted
            upserted += 1
        except Exception:
            log.exception("Error upserting bar %s %s", symbol, bar_date)

    return upserted


async def get_bars_df(session: AsyncSession, symbol: str, days: int = 504) -> pd.DataFrame:
    """Load the most recent `days` bars for a symbol as a pandas DataFrame."""
    result = await session.execute(
        select(DailyBar)
        .where(DailyBar.symbol == symbol)
        .order_by(DailyBar.bar_date.desc())
        .limit(days)
    )
    bars = result.scalars().all()
    if not bars:
        return pd.DataFrame()

    records = [
        {
            "date": b.bar_date,
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": b.volume,
            "adj_close": float(b.adj_close),
        }
        for b in bars
    ]
    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    return df
