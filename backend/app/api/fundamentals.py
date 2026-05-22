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


class AnnualPoint(BaseModel):
    year: int
    eps: float | None
    revenue: float | None         # in millions
    net_income: float | None      # in millions
    eps_yoy_pct: float | None     # vs prior year, %
    revenue_yoy_pct: float | None


class CompanySnapshot(BaseModel):
    """TradingView-style at-a-glance card."""
    name: str | None              # "Apple Inc."
    exchange: str | None          # "NMS" / "NYQ"
    industry: str | None
    sector: str | None
    country: str | None
    website: str | None
    description: str | None       # long_business_summary, may be 1-3 paragraphs
    market_cap: float | None      # in dollars
    enterprise_value: float | None
    shares_outstanding: float | None
    float_shares: float | None
    avg_volume_10d: float | None
    beta: float | None
    dividend_yield: float | None  # decimal (0.024 = 2.4%)
    dividend_rate: float | None   # annual $ per share
    payout_ratio: float | None
    price: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    ex_dividend_date: str | None


class FundamentalsOut(BaseModel):
    symbol: str
    snapshot: CompanySnapshot | None
    annual: list[AnnualPoint]          # oldest first, up to 5 years
    quarters: list[QuarterPoint]       # oldest first, ≤8 points
    # YoY from yfinance.info (most recent quarter vs same Q last year)
    eps_yoy_growth: float | None       # e.g. 0.945 = +94.5%
    revenue_yoy_growth: float | None
    # Ratios
    trailing_pe: float | None
    forward_pe: float | None
    peg_ratio: float | None
    price_to_book: float | None
    price_to_sales: float | None
    roe: float | None
    roa: float | None
    debt_to_equity: float | None
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


def _safe(val) -> float | None:
    """yfinance often returns NaN / 'Infinity' / weird types. Normalize to None."""
    try:
        if val is None:
            return None
        f = float(val)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None


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

    # Annual income statement — up to last 5 fiscal years
    annual_rows: list[dict] = []
    try:
        annual_stmt = ticker.income_stmt
        if annual_stmt is not None and not annual_stmt.empty:
            eps_row_a = next(
                (r for r in ["Basic EPS", "Diluted EPS", "Basic EPS From Continuing Operations"] if r in annual_stmt.index),
                None,
            )
            rev_row_a = "Total Revenue" if "Total Revenue" in annual_stmt.index else None
            ni_row_a  = "Net Income" if "Net Income" in annual_stmt.index else None
            cols = list(reversed(annual_stmt.columns))
            prev_eps: float | None = None
            prev_rev: float | None = None
            for col in cols:
                year = col.year
                eps_v = _safe(annual_stmt.loc[eps_row_a, col]) if eps_row_a else None
                rev_raw = _safe(annual_stmt.loc[rev_row_a, col]) if rev_row_a else None
                ni_raw  = _safe(annual_stmt.loc[ni_row_a,  col]) if ni_row_a  else None
                rev_v = rev_raw / 1_000_000 if rev_raw is not None else None
                ni_v  = ni_raw  / 1_000_000 if ni_raw  is not None else None
                eps_yoy = (eps_v - prev_eps) / abs(prev_eps) * 100 if (prev_eps not in (None, 0) and eps_v is not None) else None
                rev_yoy = (rev_v - prev_rev) / abs(prev_rev) * 100 if (prev_rev not in (None, 0) and rev_v is not None) else None
                annual_rows.append({
                    "year": year,
                    "eps": eps_v,
                    "revenue": rev_v,
                    "net_income": ni_v,
                    "eps_yoy_pct": round(eps_yoy, 1) if eps_yoy is not None else None,
                    "revenue_yoy_pct": round(rev_yoy, 1) if rev_yoy is not None else None,
                })
                prev_eps = eps_v
                prev_rev = rev_v
            # Keep at most last 5
            annual_rows = annual_rows[-5:]
    except Exception:
        log.exception("Failed to fetch annual income stmt for %s", symbol)

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

    trailing_eps = _safe(info.get("trailingEps"))
    forward_eps  = _safe(info.get("forwardEps"))
    price        = _safe(info.get("currentPrice")) or _safe(info.get("regularMarketPrice"))
    forward_pe   = round(price / forward_eps, 1) if price and forward_eps and forward_eps > 0 else None

    # Company snapshot — TradingView-style at-a-glance
    summary = info.get("longBusinessSummary") or info.get("description")
    ex_div_ts = info.get("exDividendDate")
    ex_div_str: str | None = None
    if ex_div_ts:
        try:
            ex_div_str = datetime.fromtimestamp(int(ex_div_ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            ex_div_str = None

    snapshot = {
        "name":                info.get("longName") or info.get("shortName"),
        "exchange":            info.get("exchange"),
        "industry":            info.get("industry"),
        "sector":              info.get("sector"),
        "country":             info.get("country"),
        "website":             info.get("website"),
        "description":         summary,
        "market_cap":          _safe(info.get("marketCap")),
        "enterprise_value":    _safe(info.get("enterpriseValue")),
        "shares_outstanding":  _safe(info.get("sharesOutstanding")),
        "float_shares":        _safe(info.get("floatShares")),
        "avg_volume_10d":      _safe(info.get("averageDailyVolume10Day") or info.get("averageVolume10days")),
        "beta":                _safe(info.get("beta")),
        "dividend_yield":      _safe(info.get("dividendYield")),
        "dividend_rate":       _safe(info.get("dividendRate")),
        "payout_ratio":        _safe(info.get("payoutRatio")),
        "price":               price,
        "fifty_two_week_high": _safe(info.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low":  _safe(info.get("fiftyTwoWeekLow")),
        "ex_dividend_date":    ex_div_str,
    }

    return {
        "symbol":              symbol,
        "snapshot":            snapshot,
        "annual":              annual_rows,
        "quarters":            [q.model_dump() for q in result_quarters],
        "eps_yoy_growth":      _safe(info.get("earningsQuarterlyGrowth")),
        "revenue_yoy_growth":  _safe(info.get("revenueGrowth")),
        "trailing_pe":         _safe(info.get("trailingPE")),
        "forward_pe":          forward_pe,
        "peg_ratio":           _safe(info.get("trailingPegRatio") or info.get("pegRatio")),
        "price_to_book":       _safe(info.get("priceToBook")),
        "price_to_sales":      _safe(info.get("priceToSalesTrailing12Months")),
        "roe":                 _safe(info.get("returnOnEquity")),
        "roa":                 _safe(info.get("returnOnAssets")),
        "debt_to_equity":      _safe(info.get("debtToEquity")),
        "gross_margin":        _safe(info.get("grossMargins")),
        "operating_margin":    _safe(info.get("operatingMargins")),
        "net_margin":          _safe(info.get("profitMargins")),
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
