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
from app.services.screener_service import PipelineStats, ScreenerProgress, get_screener_results, run_screener

router = APIRouter(prefix="/api/screener", tags=["screener"])

_sync_running = False
_last_stats: PipelineStats | None = None
_progress: ScreenerProgress | None = None


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

class ScanProgressOut(BaseModel):
    stage: str            # idle | starting | universe | eod | tt | vcp | fundamentals | rank | persist | done | error
    stage_label: str
    stage_index: int
    total_stages: int
    processed: int
    total: int
    pct: float            # 0-100, weighted by stage time-share
    started_at: datetime | None
    updated_at: datetime | None
    finished_at: datetime | None
    error: str | None


class SyncStatusOut(BaseModel):
    running: bool
    message: str
    stats: dict | None = None
    progress: ScanProgressOut | None = None


@router.post("/scan", response_model=SyncStatusOut)
async def run_scan(
    background_tasks: BackgroundTasks,
) -> SyncStatusOut:
    """Run a full screener scan: refresh universe, update price data, score all stocks,
    fetch missing fundamentals. Safe to run daily — price data is incremental (fast)."""
    global _sync_running, _progress
    if _sync_running:
        return SyncStatusOut(
            running=True,
            message="Scan already running — check back in a few minutes",
            progress=_progress_out(_progress),
        )
    _sync_running = True
    _progress = ScreenerProgress()
    _progress.begin()
    background_tasks.add_task(_run_sync_bg, "auto")
    return SyncStatusOut(running=True, message="Scan started", progress=_progress_out(_progress))


@router.post("/sync", response_model=SyncStatusOut)
async def trigger_sync(
    background_tasks: BackgroundTasks,
    mode: str = "auto",
) -> SyncStatusOut:
    """Legacy endpoint — use /scan instead."""
    global _sync_running, _progress
    if _sync_running:
        return SyncStatusOut(running=True, message="Sync already in progress", progress=_progress_out(_progress))
    _sync_running = True
    _progress = ScreenerProgress()
    _progress.begin()
    background_tasks.add_task(_run_sync_bg, mode)
    return SyncStatusOut(running=True, message="Scan started", progress=_progress_out(_progress))


async def _run_sync_bg(mode: str) -> None:
    global _sync_running, _last_stats, _progress
    from app.db.session import SessionLocal
    try:
        async with SessionLocal() as session:
            _, stats = await run_screener(session, mode=mode, progress=_progress)
            _last_stats = stats
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception("Background screener sync failed")
        if _progress is not None:
            _progress.fail(str(exc))
    finally:
        _sync_running = False


def _progress_out(p: ScreenerProgress | None) -> ScanProgressOut | None:
    if p is None:
        return None
    return ScanProgressOut(
        stage=p.stage_key,
        stage_label=p.stage_label,
        stage_index=p.stage_index,
        total_stages=p.total_stages,
        processed=p.processed,
        total=p.total,
        pct=round(p.overall_pct(), 1),
        started_at=p.started_at,
        updated_at=p.updated_at,
        finished_at=p.finished_at,
        error=p.error,
    )


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
        progress=_progress_out(_progress),
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
    net_income_growth: Decimal | None       # most recent quarter YoY EPS growth
    earnings_annual_growth: Decimal | None  # TTM/annual YoY (for acceleration comparison)
    net_margin: Decimal | None
    roe: Decimal | None
    eps_ttm: Decimal | None
    eps_rank: int | None
    smr_rank: int | None

    # Pattern + buyability
    pattern_type: str | None
    pattern_quality: Decimal | None
    buyability: str | None
    pivot_price: Decimal | None
    base_low: Decimal | None
    base_length_days: int | None
    base_depth_pct: Decimal | None
    extension_pct: Decimal | None

    composite_score: Decimal


class ResultsPage(BaseModel):
    items: list[ScoreOut]
    total: int
    page: int
    page_size: int
    pages: int


# Literature win-rate and avg-gain estimates for each pattern.
# Source: Bulkowski's Encyclopedia of Chart Patterns + practitioner consensus.
# These are reference numbers for the UI — not backtested on this universe.
_PATTERN_LITERATURE: dict[str, dict] = {
    "high_tight_flag":    {"label": "High Tight Flag",    "win_rate": "65-70%", "avg_gain": "+50-100%+"},
    "ascending_triangle": {"label": "Ascending Triangle", "win_rate": "~68%",   "avg_gain": "+35-45%"},
    "cwh":                {"label": "Cup w/Handle",       "win_rate": "55-60%", "avg_gain": "+30-40%"},
    "three_weeks_tight":  {"label": "3 Weeks Tight",      "win_rate": "60-65%", "avg_gain": "+25-40%"},
    "vcp":                {"label": "VCP",                 "win_rate": "55-60%", "avg_gain": "+25-50%"},
    "bull_flag":          {"label": "Bull Flag",           "win_rate": "55-60%", "avg_gain": "+20-35%"},
    "flat_base":          {"label": "Flat Base",           "win_rate": "50-55%", "avg_gain": "+20-30%"},
}


class PatternStatRow(BaseModel):
    pattern_type: str
    label: str
    count: int
    avg_quality: float
    win_rate: str
    avg_gain: str


@router.get("/pattern-stats", response_model=list[PatternStatRow])
async def pattern_stats(
    session: AsyncSession = Depends(get_session),
) -> list[PatternStatRow]:
    """Return hit counts and quality averages per pattern for the latest scan,
    alongside literature-sourced win-rate estimates."""
    from sqlalchemy import func, text
    rows = await session.execute(
        select(
            ScreenerScore.pattern_type,
            func.count().label("n"),
            func.avg(ScreenerScore.pattern_quality).label("avg_q"),
        )
        .where(ScreenerScore.pattern_type.isnot(None))
        .group_by(ScreenerScore.pattern_type)
        .order_by(func.count().desc())
    )
    out: list[PatternStatRow] = []
    for r in rows.all():
        lit = _PATTERN_LITERATURE.get(r.pattern_type, {})
        out.append(PatternStatRow(
            pattern_type=r.pattern_type,
            label=lit.get("label", r.pattern_type),
            count=r.n,
            avg_quality=round(float(r.avg_q or 0) * 100, 1),
            win_rate=lit.get("win_rate", "—"),
            avg_gain=lit.get("avg_gain", "—"),
        ))
    # Append patterns with zero hits so the table is complete
    seen = {r.pattern_type for r in out}
    for pt, lit in _PATTERN_LITERATURE.items():
        if pt not in seen:
            out.append(PatternStatRow(
                pattern_type=pt,
                label=lit["label"],
                count=0,
                avg_quality=0.0,
                win_rate=lit["win_rate"],
                avg_gain=lit["avg_gain"],
            ))
    return out


# ─── Today's Picks — tiered curated list to fight decision paralysis ──────────

class PickRow(BaseModel):
    symbol: str
    sector: str | None
    last_close: Decimal | None
    pattern_type: str | None
    pattern_quality: float        # 0-100
    buyability: str
    pivot_price: Decimal | None
    extension_pct: Decimal | None
    composite_score: float         # 0-100
    eps_rank: int | None
    rs_rank: int | None
    accelerating: bool            # earnings accelerating (Q > Annual)
    tier: str                     # "S" | "A" | "B"
    reason: str                   # 1-line "why this one"


class PicksOut(BaseModel):
    tier_s: list[PickRow]
    tier_a: list[PickRow]
    tier_b: list[PickRow]
    as_of: datetime | None
    note: str


def _is_accelerating(r: ScreenerScore) -> bool:
    """Return True if earnings show acceleration or are strongly positive.

    Preferred: compare quarterly (most-recent) vs annual (trend).
    Fallback: if annual data is missing (common after a migration before rescan),
    accept any stock with quarterly EPS growth > 25% — still a strong signal.
    """
    q = float(r.net_income_growth) if r.net_income_growth is not None else None
    a = float(r.earnings_annual_growth) if r.earnings_annual_growth is not None else None

    if q is None:
        return False
    if a is not None:
        # Full check: quarterly genuinely beating annual trend
        return q > a + 0.05 and q > 0.10
    # Fallback: annual data not yet populated — use standalone quarterly threshold
    return q > 0.25


def _pick_reason(r: ScreenerScore, tier: str) -> str:
    """Human-readable 1-line reason for the pick."""
    parts: list[str] = []
    if r.pattern_type:
        labels = {
            "high_tight_flag": "HTF", "ascending_triangle": "Ascending triangle",
            "cwh": "Cup w/Handle", "vcp": "VCP", "flat_base": "Flat base",
            "three_weeks_tight": "3 Weeks Tight", "bull_flag": "Bull flag",
        }
        q = int(round(float(r.pattern_quality or 0) * 100))
        parts.append(f"{labels.get(r.pattern_type, r.pattern_type)} q{q}")
    if r.buyability == "at_pivot":
        parts.append("at pivot")
    elif r.buyability == "in_base":
        parts.append("in base")
    if r.eps_rank is not None and r.eps_rank >= 80:
        parts.append(f"EPS {r.eps_rank}")
    if r.rs_rank is not None and r.rs_rank >= 80:
        parts.append(f"RS {r.rs_rank}")
    if _is_accelerating(r):
        parts.append("EPS accelerating")
    return " · ".join(parts) if parts else f"{r.pattern_type or 'setup'}"


def _pick_row(r: ScreenerScore, tier: str) -> PickRow:
    return PickRow(
        symbol=r.symbol,
        sector=r.sector,
        last_close=r.last_close,
        pattern_type=r.pattern_type,
        pattern_quality=round(float(r.pattern_quality or 0) * 100, 1),
        buyability=r.buyability or "no_pattern",
        pivot_price=r.pivot_price,
        extension_pct=r.extension_pct,
        composite_score=round(float(r.composite_score) * 100, 1),
        eps_rank=r.eps_rank,
        rs_rank=r.rs_rank,
        accelerating=_is_accelerating(r),
        tier=tier,
        reason=_pick_reason(r, tier),
    )


@router.get("/picks", response_model=PicksOut)
async def todays_picks(
    session: AsyncSession = Depends(get_session),
) -> PicksOut:
    """Tiered curated picks — designed to eliminate decision paralysis.

    Tier S (≤ 3):  HTF or Ascending Triangle at pivot, q ≥ 50 — highest EV
    Tier A (≤ 3):  Other patterns at pivot, q ≥ 65 AND accelerating earnings
    Tier B (≤ 4):  Quality patterns in-base (watch list, may break out today)

    Returns at most ~10 picks total, all already filtered for buyability.
    """
    q = (
        select(ScreenerScore)
        .where(ScreenerScore.buyability.in_(["at_pivot", "in_base"]))
        .where(ScreenerScore.pattern_quality.isnot(None))
        .where(ScreenerScore.pattern_quality > 0.40)
        .order_by(ScreenerScore.composite_score.desc())
    )
    result = await session.execute(q)
    rows = result.scalars().all()

    used: set[str] = set()

    # Tier S: HTF or Ascending Triangle at pivot
    tier_s: list[PickRow] = []
    for r in rows:
        if r.symbol in used: continue
        if r.buyability != "at_pivot": continue
        if r.pattern_type not in ("high_tight_flag", "ascending_triangle"): continue
        if float(r.pattern_quality) < 0.50: continue
        tier_s.append(_pick_row(r, "S"))
        used.add(r.symbol)
        if len(tier_s) >= 10: break

    # Tier A: Other patterns at pivot, quality ≥ 0.60, accelerating earnings
    tier_a: list[PickRow] = []
    for r in rows:
        if r.symbol in used: continue
        if r.buyability != "at_pivot": continue
        if r.pattern_type in ("high_tight_flag", "ascending_triangle"): continue  # already in S
        if float(r.pattern_quality) < 0.60: continue
        if not _is_accelerating(r): continue
        tier_a.append(_pick_row(r, "A"))
        used.add(r.symbol)
        if len(tier_a) >= 10: break

    # Tier B: In-base watchlist
    tier_b: list[PickRow] = []
    for r in rows:
        if r.symbol in used: continue
        if r.buyability != "in_base": continue
        if float(r.pattern_quality) < 0.55: continue
        tier_b.append(_pick_row(r, "B"))
        used.add(r.symbol)
        if len(tier_b) >= 10: break

    # Most recent scored_at as the "as of" timestamp
    as_of_q = await session.execute(select(func.max(ScreenerScore.scored_at)))
    as_of = as_of_q.scalar_one_or_none()

    return PicksOut(
        tier_s=tier_s,
        tier_a=tier_a,
        tier_b=tier_b,
        as_of=as_of,
        note="Tier S = highest expected value (HTF / Ascending Triangle). Tier A = quality bases at pivot with accelerating earnings. Tier B = quality bases in-base (watch for breakout).",
    )


@router.get("/score/{symbol}", response_model=ScoreOut)
async def get_score(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> ScoreOut:
    """Return the latest screener score for a single symbol."""
    result = await session.execute(
        select(ScreenerScore).where(ScreenerScore.symbol == symbol.upper())
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No score found for {symbol.upper()}")
    return _score_out(row)


class SearchHit(BaseModel):
    symbol: str
    sector: str | None
    tt_score: int | None              # None if unscored
    composite_score: float | None     # 0-100, None if unscored


@router.get("/search", response_model=list[SearchHit])
async def search_symbols(
    q: str = "",
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> list[SearchHit]:
    """Full-universe symbol search. Returns matches by symbol prefix, symbol
    substring, or sector substring — including stocks that aren't in the top
    composite-score window. Empty q returns the top-N by composite.
    """
    q = (q or "").strip().upper()

    # Join screener_symbols (universe) with screener_scores (latest score)
    # so stocks that haven't been scored yet still appear in search results.
    base = (
        select(
            ScreenerSymbol.symbol,
            ScreenerScore.sector,
            ScreenerScore.tt_score,
            ScreenerScore.composite_score,
        )
        .outerjoin(ScreenerScore, ScreenerScore.symbol == ScreenerSymbol.symbol)
        .where(ScreenerSymbol.is_active == True)  # noqa: E712
    )

    if not q:
        stmt = base.order_by(ScreenerScore.composite_score.desc().nullslast()).limit(limit)
    else:
        # Prefix match scores higher than substring; sector match also accepted.
        prefix = f"{q}%"
        contains = f"%{q}%"
        stmt = (
            base
            .where(
                (ScreenerSymbol.symbol.like(prefix))
                | (ScreenerSymbol.symbol.like(contains))
                | (func.upper(ScreenerScore.sector).like(contains))
            )
            # Prefix matches first, then by score
            .order_by(
                ScreenerSymbol.symbol.like(prefix).desc(),
                ScreenerScore.composite_score.desc().nullslast(),
                ScreenerSymbol.symbol.asc(),
            )
            .limit(limit)
        )

    rows = (await session.execute(stmt)).all()
    return [
        SearchHit(
            symbol=r.symbol,
            sector=r.sector,
            tt_score=r.tt_score,
            composite_score=round(float(r.composite_score) * 100, 1) if r.composite_score is not None else None,
        )
        for r in rows
    ]


@router.get("/results", response_model=ResultsPage)
async def results(
    min_tt: int = 0,
    min_vcp: float = 0.0,
    min_eps: int = 0,
    min_rs: int = 0,
    min_composite: int = 0,
    buyability: str | None = None,        # comma-separated: "at_pivot,in_base"
    pattern: str | None = None,           # comma-separated: "vcp,cwh,flat_base,high_tight_flag"
    sector: str | None = None,
    page: int = 1,
    page_size: int = 20,
    session: AsyncSession = Depends(get_session),
) -> ResultsPage:
    all_rows = await get_screener_results(
        session,
        min_tt=min_tt,
        min_vcp=min_vcp,
        min_eps=min_eps,
        min_rs=min_rs,
        min_composite=min_composite,
        buyability=buyability,
        pattern=pattern,
        sector=sector,
    )
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
        earnings_annual_growth=r.earnings_annual_growth,
        net_margin=r.net_margin,
        roe=r.roe,
        eps_ttm=r.eps_ttm,
        eps_rank=r.eps_rank,
        smr_rank=r.smr_rank,
        pattern_type=r.pattern_type,
        pattern_quality=r.pattern_quality,
        buyability=r.buyability,
        pivot_price=r.pivot_price,
        base_low=r.base_low,
        base_length_days=r.base_length_days,
        base_depth_pct=r.base_depth_pct,
        extension_pct=r.extension_pct,
        composite_score=r.composite_score,
    )
