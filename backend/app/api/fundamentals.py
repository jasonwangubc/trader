"""Fundamental data endpoint — quarterly EPS/revenue + key ratios from yfinance.

Minervini's most important criteria (in order):
  1. Quarterly EPS growth rate ≥ 25% YoY, ideally accelerating each quarter
  2. Revenue growth ≥ 25% YoY (confirms earnings aren't financial engineering)
  3. Annual EPS growth ≥ 25% for last 3 years
  4. ROE ≥ 17%
  5. Profit margin expanding (operating leverage)

The acceleration signal is the key differentiator: a stock where EPS growth went
from +20% → +35% → +60% is far more interesting than one going +60% → +35% → +20%
even if the latest number is the same. The market pays for acceleration.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/fundamentals", tags=["fundamentals"])
log = logging.getLogger(__name__)

# In-process cache: symbol → (fetched_at_ts, data)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600 * 6  # 6 hours


class QuarterPoint(BaseModel):
    period: str          # "Jan '26"
    eps: float | None
    revenue: float | None   # in millions
    eps_qoq_pct: float | None      # sequential % change
    revenue_qoq_pct: float | None
    is_eps_growing: bool | None
    is_rev_growing: bool | None


class FundamentalsOut(BaseModel):
    symbol: str
    quarters: list[QuarterPoint]       # oldest first, ≤8 points
    # YoY from yfinance.info (most recent quarter vs same Q last year)
    eps_yoy_growth: float | None       # e.g. 0.945 = +94.5%
    revenue_yoy_growth: float | None
    # Ratios
    trailing_pe: float | None
    forward_pe: float | None
    roe: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    trailing_eps: float | None
    forward_eps: float | None
    # Acceleration classification
    acceleration: str   # "explosive" | "accelerating" | "steady" | "decelerating" | "unknown"
    acceleration_note: str


def _classify_acceleration(eps_seq: list[float | None]) -> tuple[str, str]:
    """Classify EPS trend from sequential values (oldest → newest)."""
    vals = [v for v in eps_seq if v is not None and v > 0]
    if len(vals) < 3:
        return "unknown", "Insufficient quarterly data."

    # Compute sequential growth rates
    rates = []
    for i in range(1, len(vals)):
        if vals[i - 1] > 0:
            rates.append((vals[i] - vals[i - 1]) / vals[i - 1])

    if len(rates) < 2:
        return "unknown", "Need at least 3 quarters to assess acceleration."

    recent = rates[-1]
    prior  = rates[-2]
    trend  = recent - prior   # positive = accelerating

    if recent > 0.6 and trend > 0:
        return "explosive", f"EPS surging {recent*100:.0f}% this quarter and accelerating."
    if recent > 0.25 and trend > 0.05:
        return "accelerating", f"EPS growing {recent*100:.0f}% QoQ with improving momentum."
    if recent > 0.10 and abs(trend) <= 0.05:
        return "steady", f"EPS growing {recent*100:.0f}% consistently (not yet accelerating)."
    if recent > 0 and trend < -0.05:
        return "decelerating", f"EPS grew {recent*100:.0f}% but rate is slowing — watch carefully."
    if recent <= 0:
        return "decelerating", "EPS declined this quarter."
    return "steady", f"EPS trend mixed."


def _fetch_sync(symbol: str) -> dict:
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    info   = ticker.info or {}

    quarters: list[dict] = []
    try:
        stmt = ticker.quarterly_income_stmt
        if stmt is not None and not stmt.empty:
            eps_row = next(
                (r for r in ["Basic EPS", "Diluted EPS", "Basic EPS From Continuing Operations"] if r in stmt.index),
                None,
            )
            rev_row = "Total Revenue" if "Total Revenue" in stmt.index else None

            # Columns are timestamps, most recent first — reverse to oldest first
            cols = list(reversed(stmt.columns))
            for col in cols:
                period_label = col.strftime("%b '%y")
                eps = float(stmt.loc[eps_row, col]) if eps_row and eps_row in stmt.index else None
                rev_raw = float(stmt.loc[rev_row, col]) if rev_row else None
                rev = rev_raw / 1_000_000 if rev_raw else None  # convert to millions
                quarters.append({"period": period_label, "eps": eps, "revenue": rev, "col": col})
    except Exception:
        log.exception("Failed to fetch quarterly stmt for %s", symbol)

    # Compute sequential growth rates
    result_quarters: list[QuarterPoint] = []
    for i, q in enumerate(quarters):
        prev = quarters[i - 1] if i > 0 else None
        eps_qoq = None
        rev_qoq = None
        if prev:
            if prev["eps"] and prev["eps"] != 0 and q["eps"] is not None:
                eps_qoq = (q["eps"] - prev["eps"]) / abs(prev["eps"]) * 100
            if prev["revenue"] and prev["revenue"] != 0 and q["revenue"] is not None:
                rev_qoq = (q["revenue"] - prev["revenue"]) / abs(prev["revenue"]) * 100

        result_quarters.append(QuarterPoint(
            period=q["period"],
            eps=q["eps"],
            revenue=q["revenue"],
            eps_qoq_pct=round(eps_qoq, 1) if eps_qoq is not None else None,
            revenue_qoq_pct=round(rev_qoq, 1) if rev_qoq is not None else None,
            is_eps_growing=eps_qoq > 0 if eps_qoq is not None else None,
            is_rev_growing=rev_qoq > 0 if rev_qoq is not None else None,
        ))

    eps_vals = [q.eps for q in result_quarters]
    accel, accel_note = _classify_acceleration(eps_vals)

    trailing_eps = info.get("trailingEps")
    forward_eps  = info.get("forwardEps")
    price        = info.get("currentPrice") or info.get("regularMarketPrice")
    forward_pe   = round(price / forward_eps, 1) if price and forward_eps and forward_eps > 0 else None

    return {
        "symbol":              symbol,
        "quarters":            [q.model_dump() for q in result_quarters],
        "eps_yoy_growth":      info.get("earningsQuarterlyGrowth"),
        "revenue_yoy_growth":  info.get("revenueGrowth"),
        "trailing_pe":         info.get("trailingPE"),
        "forward_pe":          forward_pe,
        "roe":                 info.get("returnOnEquity"),
        "gross_margin":        info.get("grossMargins"),
        "operating_margin":    info.get("operatingMargins"),
        "net_margin":          info.get("profitMargins"),
        "trailing_eps":        trailing_eps,
        "forward_eps":         forward_eps,
        "acceleration":        accel,
        "acceleration_note":   accel_note,
    }


@router.get("/{symbol}", response_model=FundamentalsOut)
async def get_fundamentals(symbol: str) -> FundamentalsOut:
    symbol = symbol.upper()
    now = datetime.now(timezone.utc).timestamp()

    if symbol in _cache:
        fetched_at, data = _cache[symbol]
        if now - fetched_at < _CACHE_TTL:
            return FundamentalsOut(**data)

    try:
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch_sync, symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch fundamentals for {symbol}: {exc}") from exc

    _cache[symbol] = (now, data)
    return FundamentalsOut(**data)
