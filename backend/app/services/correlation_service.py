"""Correlation + sector concentration analyzer.

Reads from `daily_bars` (already populated by the EOD pipeline) and
`screener_scores` for sector metadata. Used by the wheel feature to flag
over-concentration in correlated names or in a single GICS sector.

Pure-python — no extra deps beyond what the screener already uses.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyBar, ScreenerScore

log = logging.getLogger(__name__)


@dataclass
class CorrelationPair:
    a: str
    b: str
    correlation: float        # -1 to 1
    overlap_days: int


@dataclass
class SectorBucket:
    sector: str
    symbols: list[str]
    notional: float           # sum of `notional` inputs
    pct_of_total: float       # 0-1


@dataclass
class ConcentrationReport:
    symbols: list[str]
    pairs: list[CorrelationPair]              # sorted descending by |correlation|
    sectors: list[SectorBucket]
    flagged_pairs: list[CorrelationPair] = field(default_factory=list)   # |corr| >= 0.7
    flagged_sectors: list[SectorBucket] = field(default_factory=list)    # > 35% in one sector
    single_name_warnings: list[dict] = field(default_factory=list)       # single name > 20%
    total_notional: float = 0.0


async def _load_returns(
    session: AsyncSession,
    symbols: list[str],
    lookback_days: int = 90,
) -> pd.DataFrame:
    """Return DataFrame of daily log-returns indexed by date with symbol columns."""
    if not symbols:
        return pd.DataFrame()
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days * 2)).replace(tzinfo=None)
    rows = (
        await session.execute(
            select(DailyBar.symbol, DailyBar.bar_date, DailyBar.adj_close)
            .where(DailyBar.symbol.in_(symbols))
            .where(DailyBar.bar_date >= start)
            .order_by(DailyBar.symbol, DailyBar.bar_date)
        )
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["symbol", "date", "adj_close"])
    df["adj_close"] = df["adj_close"].astype(float)
    pivot = df.pivot(index="date", columns="symbol", values="adj_close").sort_index()
    returns = pivot.pct_change().dropna(how="all")
    # Keep only the most recent `lookback_days` trading days
    return returns.tail(lookback_days)


def _compute_pairs(returns: pd.DataFrame) -> list[CorrelationPair]:
    if returns.empty or len(returns.columns) < 2:
        return []
    corr = returns.corr()
    pairs: list[CorrelationPair] = []
    cols = list(returns.columns)
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            both = returns[[a, b]].dropna()
            if both.empty:
                continue
            try:
                c = float(corr.loc[a, b])
            except (KeyError, ValueError):
                continue
            if c != c:  # NaN
                continue
            pairs.append(CorrelationPair(a=a, b=b, correlation=round(c, 3), overlap_days=len(both)))
    pairs.sort(key=lambda p: abs(p.correlation), reverse=True)
    return pairs


async def _sector_map(session: AsyncSession, symbols: list[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = (
        await session.execute(
            select(ScreenerScore.symbol, ScreenerScore.sector).where(ScreenerScore.symbol.in_(symbols))
        )
    ).all()
    return {sym: (sector or "Unknown") for sym, sector in rows}


async def correlation_report(
    session: AsyncSession,
    symbols: list[str],
    *,
    notional_by_symbol: dict[str, float] | None = None,
    lookback_days: int = 90,
    correlation_threshold: float = 0.70,
    sector_threshold: float = 0.35,
    single_name_threshold: float = 0.20,
) -> ConcentrationReport:
    """Build a concentration report for a basket of symbols.

    `notional_by_symbol` weights the sector / single-name concentration in dollars.
    If omitted, every symbol is weighted equally.
    """
    symbols = sorted({s.upper().strip() for s in symbols if s and s.strip()})
    if not symbols:
        return ConcentrationReport(symbols=[], pairs=[], sectors=[])

    # Default to equal weighting
    if notional_by_symbol is None:
        notional_by_symbol = {s: 1.0 for s in symbols}
    else:
        # Fill in any missing symbols with 0 so they appear in sectors but not totals
        notional_by_symbol = {**{s: 0.0 for s in symbols}, **{k.upper(): float(v) for k, v in notional_by_symbol.items()}}

    total = sum(notional_by_symbol.values()) or 1.0

    returns = await _load_returns(session, symbols, lookback_days=lookback_days)
    pairs = _compute_pairs(returns)
    flagged_pairs = [p for p in pairs if abs(p.correlation) >= correlation_threshold]

    sec_map = await _sector_map(session, symbols)
    by_sector: dict[str, list[str]] = defaultdict(list)
    notional_by_sector: dict[str, float] = defaultdict(float)
    for sym in symbols:
        s = sec_map.get(sym, "Unknown")
        by_sector[s].append(sym)
        notional_by_sector[s] += notional_by_symbol.get(sym, 0.0)

    sectors: list[SectorBucket] = []
    for s, syms in by_sector.items():
        notional = notional_by_sector[s]
        sectors.append(SectorBucket(
            sector=s,
            symbols=sorted(syms),
            notional=round(notional, 2),
            pct_of_total=round(notional / total, 4) if total > 0 else 0.0,
        ))
    sectors.sort(key=lambda b: b.pct_of_total, reverse=True)
    flagged_sectors = [b for b in sectors if b.pct_of_total > sector_threshold]

    single_name_warnings: list[dict] = []
    for sym in symbols:
        n = notional_by_symbol.get(sym, 0.0)
        pct = n / total if total > 0 else 0.0
        if pct > single_name_threshold:
            single_name_warnings.append({"symbol": sym, "pct_of_total": round(pct, 4), "notional": round(n, 2)})

    return ConcentrationReport(
        symbols=symbols,
        pairs=pairs,
        sectors=sectors,
        flagged_pairs=flagged_pairs,
        flagged_sectors=flagged_sectors,
        single_name_warnings=single_name_warnings,
        total_notional=round(total, 2),
    )
