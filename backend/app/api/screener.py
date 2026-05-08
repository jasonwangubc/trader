from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyBar, ScreenerScore, ScreenerSymbol
from app.db.session import get_session
from app.api.auth import get_user_id
from app.services.screener_service import PipelineStats, get_screener_results, run_screener

router = APIRouter(prefix="/api/screener", tags=["screener"])

_sync_running = False
_last_stats: PipelineStats | None = None


# ── Watchlist CRUD ────────────────────────────────────────────────────────────

class SymbolIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    notes: str | None = None


class SymbolOut(BaseModel):
    id: uuid.UUID
    symbol: str
    name: str | None
    notes: str | None
    is_active: bool
    created_at: datetime


@router.get("/watchlist", response_model=list[SymbolOut])
async def list_watchlist(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[SymbolOut]:
    result = await session.execute(
        select(ScreenerSymbol).where(
            ScreenerSymbol.is_active == True,  # noqa: E712
            ScreenerSymbol.user_id == user_id,
        ).order_by(ScreenerSymbol.symbol)
    )
    return [_sym_out(s) for s in result.scalars().all()]


@router.post("/watchlist", response_model=SymbolOut, status_code=201)
async def add_symbol(
    body: SymbolIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> SymbolOut:
    sym = body.symbol.strip().upper()
    existing = await session.execute(
        select(ScreenerSymbol).where(
            ScreenerSymbol.symbol == sym,
            ScreenerSymbol.user_id == user_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.is_active = True
        row.notes = body.notes or row.notes
    else:
        row = ScreenerSymbol(symbol=sym, notes=body.notes, user_id=user_id)
        session.add(row)
    await session.commit()
    await session.refresh(row)
    return _sym_out(row)


@router.delete("/watchlist/{symbol}", status_code=204)
async def remove_symbol(
    symbol: str,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> None:
    result = await session.execute(
        select(ScreenerSymbol).where(
            ScreenerSymbol.symbol == symbol.upper(),
            ScreenerSymbol.user_id == user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.is_active = False
        await session.commit()


def _sym_out(s: ScreenerSymbol) -> SymbolOut:
    return SymbolOut(
        id=s.id,
        symbol=s.symbol,
        name=s.name,
        notes=s.notes,
        is_active=s.is_active,
        created_at=s.created_at,
    )


# ── Sync / pipeline ───────────────────────────────────────────────────────────

class SyncStatusOut(BaseModel):
    running: bool
    message: str
    stats: dict | None = None


@router.post("/scan", response_model=SyncStatusOut)
async def run_scan(
    background_tasks: BackgroundTasks,
) -> SyncStatusOut:
    """Run a full screener scan: refresh universe, update price data, score all stocks,
    fetch missing fundamentals. Safe to run daily — price data is incremental (fast)."""
    global _sync_running
    if _sync_running:
        return SyncStatusOut(running=True, message="Scan already running — check back in a few minutes")
    _sync_running = True
    background_tasks.add_task(_run_sync_bg, "auto")
    return SyncStatusOut(running=True, message="Scan started")


@router.post("/sync", response_model=SyncStatusOut)
async def trigger_sync(
    background_tasks: BackgroundTasks,
    mode: str = "auto",
) -> SyncStatusOut:
    """Legacy endpoint — use /scan instead."""
    global _sync_running
    if _sync_running:
        return SyncStatusOut(running=True, message="Sync already in progress")
    _sync_running = True
    background_tasks.add_task(_run_sync_bg, mode)
    return SyncStatusOut(running=True, message=f"Scan started")


async def _run_sync_bg(mode: str) -> None:
    global _sync_running, _last_stats
    from app.db.session import SessionLocal
    try:
        async with SessionLocal() as session:
            _, stats = await run_screener(session, mode=mode)
            _last_stats = stats
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Background screener sync failed")
    finally:
        _sync_running = False


@router.get("/sync/status", response_model=SyncStatusOut)
async def sync_status() -> SyncStatusOut:
    stats_dict = None
    if _last_stats:
        stats_dict = {
            "universe_size": _last_stats.universe_size,
            "eod_downloaded_bars": _last_stats.eod_downloaded,
            "tt_passing": _last_stats.tt_passing,
            "scored": _last_stats.scored,
            "with_fundamentals": _last_stats.with_fundamentals,
        }
    return SyncStatusOut(
        running=_sync_running,
        message="running" if _sync_running else "idle",
        stats=stats_dict,
    )


# ── Health / data coverage ────────────────────────────────────────────────────

class PriceCoverage(BaseModel):
    symbols_total: int
    symbols_with_recent_bars: int    # bar in last 10 days
    pct_covered: float
    latest_bar_date: str | None      # "YYYY-MM-DD"
    is_stale: bool                   # True if latest bar is before last trading day
    missing_symbols: list[str]       # watchlist symbols with no recent bars


class FundamentalCoverage(BaseModel):
    symbols_scored: int
    symbols_with_fundamentals: int
    pct_covered: float
    note: str                        # explains why coverage < 100%
    top_missing: list[dict]          # top TT-scored symbols without EDGAR data


class ScoreCoverage(BaseModel):
    total_scored: int
    last_run_at: datetime | None
    tt_distribution: dict[str, int]  # {"8": 45, "7": 38, ...}


class ScreenerHealth(BaseModel):
    universe_total: int
    price: PriceCoverage
    fundamentals: FundamentalCoverage
    scores: ScoreCoverage


@router.get("/health", response_model=ScreenerHealth)
async def screener_health(session: AsyncSession = Depends(get_session)) -> ScreenerHealth:
    from datetime import timedelta
    from sqlalchemy import text

    cutoff = datetime.now(timezone.utc) - timedelta(days=10)

    # Universe
    universe_q = await session.execute(
        select(func.count()).select_from(ScreenerSymbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
    )
    universe_total = universe_q.scalar_one()

    # Active watchlist symbols
    sym_q = await session.execute(
        select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
    )
    all_syms = {r for (r,) in sym_q.all()}

    # Symbols with recent bars
    recent_q = await session.execute(
        select(func.distinct(DailyBar.symbol)).where(DailyBar.bar_date >= cutoff.date())
    )
    recent_syms = {r for (r,) in recent_q.all()}
    missing_price = sorted(all_syms - recent_syms)

    # Latest bar date
    latest_q = await session.execute(select(func.max(DailyBar.bar_date)))
    latest_bar = latest_q.scalar_one()

    # Scored symbols
    scored_q = await session.execute(select(func.count()).select_from(ScreenerScore))
    scored_total = scored_q.scalar_one()

    # With fundamentals
    fund_q = await session.execute(
        select(func.count()).select_from(ScreenerScore).where(ScreenerScore.fundamental_score > 0)
    )
    fund_total = fund_q.scalar_one()

    # Last scored
    last_q = await session.execute(select(func.max(ScreenerScore.scored_at)))
    last_scored = last_q.scalar_one()

    # Top missing fundamentals (TT >= 5, no fundamental data)
    missing_fund_q = await session.execute(
        select(ScreenerScore.symbol, ScreenerScore.tt_score, ScreenerScore.vcp_score, ScreenerScore.rs_rank)
        .where(ScreenerScore.fundamental_score == 0, ScreenerScore.tt_score >= 5)
        .order_by(ScreenerScore.tt_score.desc(), ScreenerScore.vcp_score.desc())
        .limit(20)
    )
    top_missing_fund = [
        {"symbol": r.symbol, "tt_score": r.tt_score,
         "vcp_score": float(r.vcp_score), "rs_rank": r.rs_rank}
        for r in missing_fund_q.all()
    ]

    # TT distribution
    tt_q = await session.execute(
        select(ScreenerScore.tt_score, func.count()).group_by(ScreenerScore.tt_score)
        .order_by(ScreenerScore.tt_score.desc())
    )
    tt_dist = {str(row.tt_score): row.count for row in tt_q.all()}

    price_pct = round(len(recent_syms) / max(universe_total, 1) * 100, 1)
    fund_pct = round(fund_total / max(scored_total, 1) * 100, 1)

    from app.services.nightly_service import _is_stale as _price_stale
    latest_date = latest_bar.date() if latest_bar and hasattr(latest_bar, "date") else latest_bar

    return ScreenerHealth(
        universe_total=universe_total,
        price=PriceCoverage(
            symbols_total=universe_total,
            symbols_with_recent_bars=len(recent_syms),
            pct_covered=price_pct,
            latest_bar_date=str(latest_bar)[:10] if latest_bar else None,
            is_stale=_price_stale(latest_date) if latest_date else True,
            missing_symbols=missing_price[:30],
        ),
        fundamentals=FundamentalCoverage(
            symbols_scored=scored_total,
            symbols_with_fundamentals=fund_total,
            pct_covered=fund_pct,
            note=(
                "Canadian stocks (TSX-listed) don't file with the SEC, so they will never "
                "have EDGAR fundamental data. US stocks missing data will be fetched on next scan."
            ),
            top_missing=top_missing_fund,
        ),
        scores=ScoreCoverage(
            total_scored=scored_total,
            last_run_at=last_scored,
            tt_distribution=tt_dist,
        ),
    )


# ── Results ───────────────────────────────────────────────────────────────────

class ScoreOut(BaseModel):
    symbol: str
    scored_at: datetime
    sector: str | None
    universe_source: str | None
    tt_score: int
    tt_criteria: dict
    vcp_score: Decimal
    vcp_details: dict
    rs_rank: int | None
    rs_raw: Decimal | None
    last_close: Decimal | None
    ma_50: Decimal | None
    ma_150: Decimal | None
    ma_200: Decimal | None
    high_52w: Decimal | None
    low_52w: Decimal | None
    fundamental_score: Decimal
    revenue_growth: Decimal | None
    net_income_growth: Decimal | None
    net_margin: Decimal | None
    eps_ttm: Decimal | None
    composite_score: Decimal


class ResultsPage(BaseModel):
    items: list[ScoreOut]
    total: int
    page: int
    page_size: int
    pages: int


@router.get("/results", response_model=ResultsPage)
async def results(
    min_tt: int = 0,
    min_vcp: float = 0.0,
    sector: str | None = None,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> ResultsPage:
    all_rows = await get_screener_results(session, min_tt=min_tt, min_vcp=min_vcp, sector=sector)
    total = len(all_rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page  = max(1, min(page, pages))
    start = (page - 1) * page_size
    rows  = all_rows[start : start + page_size]
    return ResultsPage(
        items=[_score_out(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        pages=pages,
    )


def _score_out(r: ScreenerScore) -> ScoreOut:
    return ScoreOut(
        symbol=r.symbol,
        scored_at=r.scored_at,
        sector=r.sector,
        universe_source=r.universe_source,
        tt_score=r.tt_score,
        tt_criteria=r.tt_criteria or {},
        vcp_score=r.vcp_score,
        vcp_details=r.vcp_details or {},
        rs_rank=r.rs_rank,
        rs_raw=r.rs_raw,
        last_close=r.last_close,
        ma_50=r.ma_50,
        ma_150=r.ma_150,
        ma_200=r.ma_200,
        high_52w=r.high_52w,
        low_52w=r.low_52w,
        fundamental_score=r.fundamental_score,
        revenue_growth=r.revenue_growth,
        net_income_growth=r.net_income_growth,
        net_margin=r.net_margin,
        eps_ttm=r.eps_ttm,
        composite_score=r.composite_score,
    )
