"""End-of-day data pipeline using yfinance.

Downloads adjusted OHLCV for all active screener symbols plus benchmark tickers
(SPY for US, XIU.TO for TSX). Stores in daily_bars. Safe to run repeatedly — uses
upsert logic so re-runs are idempotent.

Lookback: 2 years (504 trading days) to support 200-day MA and 52-week stats.
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyBar, ScreenerSymbol
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

BENCHMARKS = ["SPY", "XIU.TO"]   # RS comparison universe
LOOKBACK_YEARS = 2

# yfinance bulk-download tuning.  A single yf.download() call for thousands of
# symbols can trigger rate limits and partial failures.  We split into chunks
# so that a rate-limited chunk only loses ~CHUNK_SIZE symbols, not the whole
# universe, and we retry each chunk before giving up.
_CHUNK_SIZE = 500
_INTER_CHUNK_DELAY = 2.0   # seconds between chunks
_MAX_RETRIES = 2
_RETRY_DELAY = 10.0        # seconds before first retry (doubled on second)


async def sync_eod_incremental(
    session: AsyncSession,
    symbols: list[str],
    *,
    full_years: int = 2,
    delta_days: int = 35,
    on_chunk: Callable[[int], None] | None = None,
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
        counts.update(await _bulk_download_and_store(session, new_symbols, cutoff_full, today, on_chunk=on_chunk))

    # Existing: only delta.
    if existing_symbols:
        log.info("Delta download for %d existing symbols (last %d days)", len(existing_symbols), delta_days)
        counts.update(await _bulk_download_and_store(session, existing_symbols, cutoff_delta, today, on_chunk=on_chunk))

    await session.commit()
    return counts


async def _download_chunk(
    symbols: list[str],
    start: str,
    end: str,
) -> pd.DataFrame | None:
    """Download one chunk from yfinance, retrying up to _MAX_RETRIES times.

    Returns the raw DataFrame, or None if all attempts fail.
    """
    loop = asyncio.get_event_loop()
    for attempt in range(_MAX_RETRIES + 1):
        try:
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
            return raw
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                delay = _RETRY_DELAY * (2 ** attempt)
                log.warning(
                    "yfinance chunk download failed (%d/%d), retrying in %.0fs: %s",
                    attempt + 1, _MAX_RETRIES + 1, delay, exc,
                )
                await asyncio.sleep(delay)
            else:
                log.error(
                    "yfinance chunk download failed after %d attempts for %d symbols: %s",
                    _MAX_RETRIES + 1, len(symbols), exc,
                )
    return None


async def _bulk_download_and_store(
    session: AsyncSession,
    symbols: list[str],
    start: str,
    end: str,
    on_chunk: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Download symbols in chunks and upsert bars into daily_bars.

    Chunked so a rate-limit or timeout only loses one chunk, not the whole
    universe. Each chunk is retried before being abandoned.

    on_chunk: called after each chunk completes with the number of symbols in
    that chunk, so a progress meter can advance.
    """
    if not symbols:
        return {}

    counts: dict[str, int] = {s: 0 for s in symbols}
    chunks = [symbols[i : i + _CHUNK_SIZE] for i in range(0, len(symbols), _CHUNK_SIZE)]
    log.info(
        "Downloading %d symbols in %d chunk(s) of up to %d",
        len(symbols), len(chunks), _CHUNK_SIZE,
    )

    for chunk_idx, chunk in enumerate(chunks):
        if chunk_idx > 0:
            await asyncio.sleep(_INTER_CHUNK_DELAY)

        raw = await _download_chunk(chunk, start, end)
        if raw is None:
            log.error("Skipping chunk %d/%d — all retries exhausted", chunk_idx + 1, len(chunks))
        else:
            for sym in chunk:
                try:
                    counts[sym] = await _upsert_symbol_bars(session, sym, raw, chunk)
                except Exception:
                    log.exception("Failed to upsert bars for %s", sym)

            stored = sum(1 for s in chunk if counts.get(s, 0) > 0)
            log.info(
                "Chunk %d/%d: %d/%d symbols had data",
                chunk_idx + 1, len(chunks), stored, len(chunk),
            )

        if on_chunk is not None:
            try:
                on_chunk(len(chunk))
            except Exception:
                pass

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
    """Extract one symbol from the yfinance DataFrame and upsert into daily_bars.

    Uses a single bulk INSERT … ON CONFLICT (symbol, bar_date) DO UPDATE so
    the operation is atomic and safe against:
      - duplicate date rows in yfinance source data
      - repeated calls for the same symbol within one transaction
      - concurrent writes (unlikely but safe regardless)
    """
    try:
        if hasattr(raw.columns, "levels"):
            if symbol in raw.columns.get_level_values(1):
                df = raw.xs(symbol, axis=1, level=1).copy()
            else:
                df = pd.DataFrame()
        else:
            df = raw.copy()
    except Exception:
        log.warning("No data in download for %s", symbol)
        return 0

    if df.empty or len(df) < 2:
        log.warning("Insufficient data for %s (%d rows)", symbol, len(df))
        return 0

    df = df.dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index)

    rows: list[dict] = []
    seen_dates: set = set()
    for bar_date, row in df.iterrows():
        try:
            dt = bar_date.to_pydatetime().replace(tzinfo=None)
            if dt in seen_dates:
                continue  # deduplicate within the yfinance response itself
            seen_dates.add(dt)
            close = Decimal(str(round(float(row["Close"]), 6)))
            rows.append({
                "id":        _uuid.uuid4(),
                "symbol":    symbol,
                "bar_date":  dt,
                "open":      Decimal(str(round(float(row.get("Open",   row["Close"])), 6))),
                "high":      Decimal(str(round(float(row.get("High",   row["Close"])), 6))),
                "low":       Decimal(str(round(float(row.get("Low",    row["Close"])), 6))),
                "close":     close,
                "volume":    int(row.get("Volume", 0)),
                "adj_close": close,  # auto_adjust=True — Close is already adjusted
            })
        except Exception:
            log.exception("Error preparing bar %s %s", symbol, bar_date)

    if not rows:
        return 0

    stmt = pg_insert(DailyBar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "bar_date"],
        set_={
            "open":      stmt.excluded.open,
            "high":      stmt.excluded.high,
            "low":       stmt.excluded.low,
            "close":     stmt.excluded.close,
            "volume":    stmt.excluded.volume,
            "adj_close": stmt.excluded.adj_close,
        },
    )
    await session.execute(stmt)
    return len(rows)


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
