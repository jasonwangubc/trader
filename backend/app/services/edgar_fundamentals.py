"""EDGAR fundamentals fetcher — adapted from the decadex project.

Pulls key screening metrics from SEC's free companyfacts API
(data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json) — the same source
decadex uses. No API key required, no rate limits (with respectful pacing).

We extract only what's needed for Minervini-style screening:
  - Revenue (trailing 4Q → TTM, YoY growth)
  - Net income / EPS (TTM, YoY growth)
  - Net margin (net_income / revenue)

These three capture earnings momentum and quality — the #1 Minervini filter.

XBRL tag mapping is borrowed directly from decadex's TAG_PRIORITIES dict.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx

log = logging.getLogger(__name__)

EDGAR_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
def _user_agent() -> str:
    from app.config import get_settings
    return get_settings().edgar_user_agent


def _get_user_agent() -> str:
    return _user_agent()
REQUEST_DELAY = 0.12   # 8 requests/sec — SEC asks for ≤ 10/sec

_REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueGoodsNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenuesNetOfInterestExpense",
    "InterestAndDividendIncomeOperating",  # banks
    "BrokerageCommissionsRevenue",
    "HealthCareOrganizationRevenue",
    "OilAndGasRevenue",
]
_NET_INCOME_TAGS = [
    "NetIncomeLoss",
    "NetIncome",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAvailableToCommonStockholdersDiluted",
    "IncomeLossFromContinuingOperations",
]
_EPS_TAGS = [
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
    "EarningsPerShareBasicAndDiluted",
    "IncomeLossFromContinuingOperationsPerBasicShare",
]


@dataclass
class FundamentalSnapshot:
    """Screening-relevant fundamentals for one symbol, computed from EDGAR data."""
    symbol: str
    cik: str
    # TTM metrics
    revenue_ttm: float | None = None
    net_income_ttm: float | None = None
    eps_ttm: float | None = None
    # YoY growth (fractional: 0.20 = +20%)
    revenue_growth: float | None = None
    net_income_growth: float | None = None
    # Derived
    net_margin: float | None = None
    # Composite fundamental score 0–1
    score: float = 0.0
    error: str | None = None


def _extract_all_entries(facts: dict, tags: list[str]) -> list[dict]:
    """Pull all USD entries for the first matching tag (both annual and quarterly)."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        entries = (
            us_gaap.get(tag, {}).get("units", {}).get("USD", [])
            or us_gaap.get(tag, {}).get("units", {}).get("shares", [])
        )
        valid = [e for e in entries if e.get("val") is not None and e.get("end") and e.get("start")]
        if valid:
            return valid
    return []


def _extract_annual_series(facts: dict, tags: list[str]) -> list[tuple[date, float]]:
    """Pull annual (10-K) values — most recent available per fiscal year."""
    entries = _extract_all_entries(facts, tags)
    annual = [e for e in entries if e.get("form") in ("10-K", "20-F", "40-F")]
    if not annual:
        return []
    by_date: dict[date, float] = {}
    for e in annual:
        try:
            d = date.fromisoformat(e["end"])
            val = float(e["val"])
            if d not in by_date or abs(val) > abs(by_date[d]):
                by_date[d] = val
        except (ValueError, TypeError):
            continue
    return sorted(by_date.items())


def _extract_quarterly_series(facts: dict, tags: list[str]) -> list[tuple[date, float]]:
    """Extract individual quarterly values from EDGAR, handling YTD reporting.

    10-Q filings often report YTD (year-to-date) values rather than single quarters:
      Q1 10-Q: period ~90 days  → already a single quarter
      Q2 10-Q: period ~180 days → H1, derive Q2 = H1 - Q1
      Q3 10-Q: period ~270 days → 9M, derive Q3 = 9M - H1
      10-K:    period ~365 days → Annual, derive Q4 = Annual - 9M

    Returns: list of (quarter_end_date, value) sorted oldest-first.
    """
    entries = _extract_all_entries(facts, tags)
    if not entries:
        return []

    # Build a lookup: (start, end) → value
    by_period: dict[tuple[date, date], float] = {}
    for e in entries:
        try:
            s = date.fromisoformat(e["start"])
            d = date.fromisoformat(e["end"])
            val = float(e["val"])
            key = (s, d)
            if key not in by_period or abs(val) > abs(by_period[key]):
                by_period[key] = val
        except (ValueError, TypeError):
            continue

    # Sort by end date
    periods = sorted(by_period.items(), key=lambda x: x[0][1])

    # Find single-quarter entries (period ≈ 85-95 days)
    quarters: dict[date, float] = {}

    for (start, end), val in periods:
        days = (end - start).days
        if 75 <= days <= 105:
            # Single quarter (Q1 or standalone quarterly filing)
            quarters[end] = val
        elif 165 <= days <= 200:
            # H1 (Q1+Q2) — derive Q2 = H1 - Q1
            # Find the matching Q1 (end ~90 days before H1-end)
            for (qs, qe), qv in by_period.items():
                if abs((end - qe).days - 90) < 20 and qs.year == start.year:
                    quarters[end] = val - qv
                    break
        elif 255 <= days <= 290:
            # 9M (Q1+Q2+Q3) — derive Q3 = 9M - H1
            for (hs, he), hv in by_period.items():
                if abs((end - he).days - 90) < 20 and hs.year == start.year:
                    quarters[end] = val - hv
                    break
        elif 340 <= days <= 390:
            # Annual 10-K — derive Q4 = Annual - 9M
            for (ns, ne), nv in by_period.items():
                if abs((end - ne).days - 90) < 20 and ns.year == start.year:
                    quarters[end] = val - nv
                    break

    # Return sorted with at most 12 quarters
    result = sorted(quarters.items())
    return result[-12:]  # last 3 years


def _compute_snapshot(symbol: str, cik: str, facts: dict) -> FundamentalSnapshot:
    snap = FundamentalSnapshot(symbol=symbol, cik=cik)

    # Try quarterly first (more current + Minervini cares about quarterly acceleration)
    rev_q  = _extract_quarterly_series(facts, _REVENUE_TAGS)
    ni_q   = _extract_quarterly_series(facts, _NET_INCOME_TAGS)
    eps_q  = _extract_quarterly_series(facts, _EPS_TAGS)

    # Fall back to annual if quarterly extraction fails
    rev_a  = _extract_annual_series(facts, _REVENUE_TAGS)
    ni_a   = _extract_annual_series(facts, _NET_INCOME_TAGS)
    eps_a  = _extract_annual_series(facts, _EPS_TAGS)

    # -- Revenue --
    if len(rev_q) >= 2:
        # Most recent quarter vs same quarter 1 year ago (YoY quarterly growth)
        snap.revenue_ttm = rev_q[-1][1]
        # Find matching quarter ~4 quarters back
        target_date = rev_q[-1][0]
        year_ago = [v for d, v in rev_q if abs((target_date - d).days - 365) < 60]
        if year_ago and year_ago[0] and year_ago[0] != 0:
            snap.revenue_growth = (snap.revenue_ttm - year_ago[-1]) / abs(year_ago[-1])
    elif len(rev_a) >= 2:
        snap.revenue_ttm = rev_a[-1][1]
        prev = rev_a[-2][1]
        if prev and prev != 0:
            snap.revenue_growth = (snap.revenue_ttm - prev) / abs(prev)
    elif rev_a:
        snap.revenue_ttm = rev_a[-1][1]

    # -- Net income --
    if len(ni_q) >= 2:
        snap.net_income_ttm = ni_q[-1][1]
        target_date = ni_q[-1][0]
        year_ago = [v for d, v in ni_q if abs((target_date - d).days - 365) < 60]
        if year_ago and year_ago[-1] and year_ago[-1] != 0:
            snap.net_income_growth = (snap.net_income_ttm - year_ago[-1]) / abs(year_ago[-1])
    elif len(ni_a) >= 2:
        snap.net_income_ttm = ni_a[-1][1]
        prev = ni_a[-2][1]
        if prev and prev != 0:
            snap.net_income_growth = (snap.net_income_ttm - prev) / abs(prev)
    elif ni_a:
        snap.net_income_ttm = ni_a[-1][1]

    # -- EPS --
    if eps_q:
        snap.eps_ttm = eps_q[-1][1]
    elif eps_a:
        snap.eps_ttm = eps_a[-1][1]

    # -- Derived --
    if snap.revenue_ttm and snap.revenue_ttm != 0 and snap.net_income_ttm is not None:
        snap.net_margin = snap.net_income_ttm / snap.revenue_ttm

    # -- Minervini score 0-4 → 0.0-1.0 --
    score = 0
    if snap.revenue_growth is not None and snap.revenue_growth > 0.10:
        score += 1
    if snap.net_income_growth is not None and snap.net_income_growth > 0.15:
        score += 1
    if snap.net_margin is not None and snap.net_margin > 0.10:
        score += 1
    if snap.eps_ttm is not None and snap.eps_ttm > 0:
        score += 1
    snap.score = round(score / 4.0, 3)

    return snap


async def fetch_fundamentals(cik: str, symbol: str) -> FundamentalSnapshot:
    """Fetch EDGAR companyfacts and return a FundamentalSnapshot."""
    url = f"{EDGAR_BASE}/CIK{cik}.json"
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": _user_agent()},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            if r.status_code == 404:
                return FundamentalSnapshot(symbol=symbol, cik=cik, error="not found in EDGAR")
            r.raise_for_status()
            facts = r.json()
    except Exception as exc:
        return FundamentalSnapshot(symbol=symbol, cik=cik, error=str(exc))

    return _compute_snapshot(symbol, cik, facts)


async def fetch_fundamentals_batch(
    symbol_cik_pairs: list[tuple[str, str]],
    concurrency: int = 5,
) -> dict[str, FundamentalSnapshot]:
    """Fetch fundamentals for multiple symbols with rate-limited concurrency."""
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, FundamentalSnapshot] = {}

    async def _fetch_one(symbol: str, cik: str) -> None:
        async with sem:
            snap = await fetch_fundamentals(cik, symbol)
            results[symbol] = snap
            await asyncio.sleep(REQUEST_DELAY)

    await asyncio.gather(*[_fetch_one(s, c) for s, c in symbol_cik_pairs])
    return results
