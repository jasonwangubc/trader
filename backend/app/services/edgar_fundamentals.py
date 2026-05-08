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
]
_NET_INCOME_TAGS = [
    "NetIncomeLoss",
    "NetIncome",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
]
_EPS_TAGS = [
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
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


def _extract_annual_series(facts: dict, tags: list[str]) -> list[tuple[date, float]]:
    """Pull annual (10-K) values for the first matching tag."""
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        node = us_gaap.get(tag, {})
        units = node.get("units", {})
        # Revenue/net income are in USD
        entries = units.get("USD", units.get("shares", []))
        annual = [
            e for e in entries
            if e.get("form") in ("10-K", "20-F", "40-F")
            and e.get("val") is not None
            and e.get("end")
        ]
        if annual:
            # Sort and deduplicate by end date (keep max val for ties)
            by_date: dict[date, float] = {}
            for e in annual:
                try:
                    d = date.fromisoformat(e["end"])
                    val = float(e["val"])
                    if d not in by_date or abs(val) > abs(by_date[d]):
                        by_date[d] = val
                except (ValueError, TypeError):
                    continue
            series = sorted(by_date.items())
            if series:
                return series
    return []


def _compute_snapshot(symbol: str, cik: str, facts: dict) -> FundamentalSnapshot:
    snap = FundamentalSnapshot(symbol=symbol, cik=cik)

    rev_series = _extract_annual_series(facts, _REVENUE_TAGS)
    ni_series  = _extract_annual_series(facts, _NET_INCOME_TAGS)
    eps_series = _extract_annual_series(facts, _EPS_TAGS)

    # TTM = most recent annual value (10-K gives the full year)
    if rev_series:
        snap.revenue_ttm = rev_series[-1][1]
        if len(rev_series) >= 2:
            prev = rev_series[-2][1]
            if prev and prev != 0:
                snap.revenue_growth = (snap.revenue_ttm - prev) / abs(prev)

    if ni_series:
        snap.net_income_ttm = ni_series[-1][1]
        if len(ni_series) >= 2:
            prev = ni_series[-2][1]
            if prev and prev != 0:
                snap.net_income_growth = (snap.net_income_ttm - prev) / abs(prev)

    if eps_series:
        snap.eps_ttm = eps_series[-1][1]

    if snap.revenue_ttm and snap.revenue_ttm != 0 and snap.net_income_ttm is not None:
        snap.net_margin = snap.net_income_ttm / snap.revenue_ttm

    # Score 0-4 → normalise to 0-1
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
