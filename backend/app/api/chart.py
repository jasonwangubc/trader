"""Chart data endpoint — OHLCV + pre-computed indicators for a symbol.

Returns data in the format expected by TradingView's lightweight-charts:
  bars:   [{time, open, high, low, close, volume}]
  sma50:  [{time, value}]
  sma150: [{time, value}]
  sma200: [{time, value}]
  rs:     [{time, value}]   — price ratio relative to SPY (RS line, not rank)
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.eod_service import get_bars_df

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chart", tags=["chart"])


class CandleBar(BaseModel):
    time: str   # "YYYY-MM-DD"
    open: float
    high: float
    low: float
    close: float
    volume: int


class LinePoint(BaseModel):
    time: str
    value: float


class ChartData(BaseModel):
    symbol: str
    bars: list[CandleBar]
    sma50:  list[LinePoint]
    sma150: list[LinePoint]
    sma200: list[LinePoint]
    rs:     list[LinePoint]   # RS line vs SPY (stock / SPY, normalised to 100 at start)
    pivot:  float | None      # detected pivot price
    base_start: str | None    # ISO date where current base began


def _sma(closes: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(closes), np.nan)
    for i in range(period - 1, len(closes)):
        result[i] = float(np.mean(closes[i - period + 1 : i + 1]))
    return result


def _detect_pivot(df) -> tuple[float | None, str | None]:
    """Identify the pivot breakout price within the current VCP base.

    Algorithm: look at the last 15-65 bars for a tightening range. The pivot
    is the intraday high of the most recent tight contraction (last 3-10 bars
    where the daily range < 1% of the high). Failing that, use the 52-week high
    of the most recent 25-bar window.
    """
    if df.empty or len(df) < 20:
        return None, None

    closes = df["close"].values.astype(float)
    highs  = df["high"].values.astype(float)
    lows   = df["low"].values.astype(float)
    dates  = df["date"].tolist()

    # Look at last 65 bars for the base
    window = min(65, len(closes))
    h = highs[-window:]
    l = lows[-window:]
    c = closes[-window:]
    d = dates[-window:]

    # Find a contraction: rolling 5-bar range < 8% from the base high
    base_high = float(np.max(h))
    base_low  = float(np.min(l))
    base_depth = (base_high - base_low) / base_high if base_high > 0 else 1.0

    # Base start: first bar where price dipped from the base high
    peak_idx = int(np.argmax(h))
    base_start_date = str(d[peak_idx]) if peak_idx < len(d) else None

    # Recent tightest 10-bar window — pivot is the high of that window
    if len(h) >= 10:
        tight_window = 10
        ranges = [(i, (h[i] - l[i]) / h[i]) for i in range(len(h) - tight_window, len(h))]
        avg_range = float(np.mean([r for _, r in ranges]))
        if avg_range < 0.02:  # very tight
            pivot = float(max(h[-(tight_window):]))
        else:
            pivot = float(np.max(h[-25:])) if len(h) >= 25 else float(np.max(h))
    else:
        pivot = float(np.max(h))

    return round(pivot * 1.005, 2), base_start_date  # pivot = base high + 0.5% buffer


@router.get("/{symbol}", response_model=ChartData)
async def chart(
    symbol: str,
    days: int = 504,
    session: AsyncSession = Depends(get_session),
) -> ChartData:
    symbol = symbol.upper()
    df = await get_bars_df(session, symbol, days=days)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}. Run a screener scan first.")

    spy_df = await get_bars_df(session, "SPY", days=days)

    closes = df["close"].values.astype(float)
    dates  = [str(d.date()) if hasattr(d, "date") else str(d)[:10] for d in df["date"].tolist()]

    sma50_vals  = _sma(closes, 50)
    sma150_vals = _sma(closes, 150)
    sma200_vals = _sma(closes, 200)

    # RS line: (symbol / SPY) normalised to 100 at the start of the series.
    # Align on common dates.
    spy_close_by_date: dict[str, float] = {}
    if not spy_df.empty:
        for _, row in spy_df.iterrows():
            dt = str(row["date"].date()) if hasattr(row["date"], "date") else str(row["date"])[:10]
            spy_close_by_date[dt] = float(row["close"])

    rs_line: list[LinePoint] = []
    if spy_close_by_date:
        first_ratio = None
        for dt, c in zip(dates, closes):
            spy_c = spy_close_by_date.get(dt)
            if spy_c and spy_c > 0:
                ratio = c / spy_c
                if first_ratio is None:
                    first_ratio = ratio
                if first_ratio and first_ratio > 0:
                    rs_line.append(LinePoint(time=dt, value=round(ratio / first_ratio * 100, 4)))

    pivot, base_start = _detect_pivot(df)

    bars = [
        CandleBar(
            time=dt,
            open=round(float(df["open"].iloc[i]), 4),
            high=round(float(df["high"].iloc[i]), 4),
            low=round(float(df["low"].iloc[i]), 4),
            close=round(float(c), 4),
            volume=int(df["volume"].iloc[i]),
        )
        for i, (dt, c) in enumerate(zip(dates, closes))
    ]

    def make_line(vals: np.ndarray) -> list[LinePoint]:
        return [
            LinePoint(time=dt, value=round(float(v), 4))
            for dt, v in zip(dates, vals)
            if not np.isnan(v)
        ]

    return ChartData(
        symbol=symbol,
        bars=bars,
        sma50=make_line(sma50_vals),
        sma150=make_line(sma150_vals),
        sma200=make_line(sma200_vals),
        rs=rs_line,
        pivot=pivot,
        base_start=base_start,
    )
