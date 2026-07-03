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

from app.api.auth import get_user_id
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


def _pattern_pivot(df, sma50_vals: np.ndarray, sma200_vals: np.ndarray) -> tuple[float | None, str | None]:
    """Pivot + base start via pattern_service.detect_pattern — the same
    single source of truth the screener persists, so the chart page and the
    screener table can never disagree.
    """
    from app.services.pattern_service import detect_pattern

    if df.empty or len(df) < 20:
        return None, None

    ma50_last = float(sma50_vals[-1]) if len(sma50_vals) and not np.isnan(sma50_vals[-1]) else None
    ma200_last = float(sma200_vals[-1]) if len(sma200_vals) and not np.isnan(sma200_vals[-1]) else None
    pat = detect_pattern(df, ma_50=ma50_last, ma_200=ma200_last)

    base_start_date: str | None = None
    if pat.base_length_days is not None and pat.base_length_days < len(df):
        raw = df["date"].iloc[len(df) - 1 - pat.base_length_days]
        base_start_date = (
            raw.strftime("%Y-%m-%d") if hasattr(raw, "strftime") else str(raw)[:10]
        )
    return pat.pivot_price, base_start_date


class StopOptionOut(BaseModel):
    method: str
    price: float
    distance_pct: float
    description: str


class TargetOut(BaseModel):
    label: str
    r_multiple: float
    price: float
    p_20d: float
    p_40d: float


class RecommendationsOut(BaseModel):
    symbol: str
    entry_price: float
    stops: list[StopOptionOut]
    recommended_stop: StopOptionOut | None
    targets: list[TargetOut]
    atr_14: float
    base_low: float | None
    annual_vol_pct: float
    daily_drift: float
    expected_value_20d: float


@router.get("/{symbol}/recommendations", response_model=RecommendationsOut)
async def recommendations(
    symbol: str,
    session: AsyncSession = Depends(get_session),
) -> RecommendationsOut:
    from app.services.recommendations_service import compute_recommendations
    symbol = symbol.upper()
    df = await get_bars_df(session, symbol, days=252)
    if df.empty:
        await _fetch_on_demand(session, symbol)
        df = await get_bars_df(session, symbol, days=252)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}.")
    import asyncio
    loop = asyncio.get_event_loop()
    rec = await loop.run_in_executor(None, compute_recommendations, df, symbol)
    return RecommendationsOut(
        symbol=rec.symbol,
        entry_price=rec.entry_price,
        stops=[StopOptionOut(**s.__dict__) for s in rec.stops],
        recommended_stop=StopOptionOut(**rec.recommended_stop.__dict__) if rec.recommended_stop else None,
        targets=[TargetOut(**t.__dict__) for t in rec.targets],
        atr_14=rec.atr_14,
        base_low=rec.base_low,
        annual_vol_pct=rec.annual_vol_pct,
        daily_drift=rec.daily_drift,
        expected_value_20d=rec.expected_value_20d,
    )


class TradePlanOddsOut(BaseModel):
    available: bool
    reason: str | None
    cohort: str
    widened: bool
    n_setups: int
    n_triggered: int
    trigger_rate: float
    target_pct: float
    stop_pct: float
    time_pct: float
    avg_r: float
    time_avg_r: float
    stop_atr: float
    target_r: float
    time_stop_days: int
    scan_date: str | None
    caveats: list[str]


class TradePlanSizingOut(BaseModel):
    shares: int
    risk_amount: float
    risk_pct: float
    equity_basis: float
    equity_currency: str
    warnings: list[str]


class TradePlanOut(BaseModel):
    symbol: str
    pattern_type: str | None
    pattern_quality: float | None
    buyability: str | None
    tier: str                      # S / A / B / "" for this setup
    pivot: float | None            # buy trigger (breakout point)
    stop: float | None
    stop_method: str | None        # base_low / atr / pct
    target: float | None
    target_r: float | None         # target expressed as an R-multiple of risk
    last_close: float | None
    atr14: float | None
    sizing: TradePlanSizingOut | None   # from the active trading account, if set
    odds: TradePlanOddsOut


@router.get("/{symbol}/trade-plan", response_model=TradePlanOut)
async def trade_plan(
    symbol: str,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TradePlanOut:
    """One-stop trade plan for a ticker: pivot / stop / first target, position
    size from the active trading account, and the empirical odds of hitting
    the target before the stop based on similar historical setups."""
    from decimal import Decimal

    from sqlalchemy import select as sa_select

    from app.api.screener import suggest_entry
    from app.db.models import ScreenerScore
    from app.services.accounts_service import get_active_account_id
    from app.services.backtest_service import _classify_tier
    from app.services.odds_service import compute_outcome_odds
    from app.services.tickets_service import TicketValidationError, preview_ticket

    sym = symbol.upper()
    suggest = await suggest_entry(sym, session)  # raises 404 if nothing known

    row = (
        await session.execute(sa_select(ScreenerScore).where(ScreenerScore.symbol == sym))
    ).scalar_one_or_none()
    pattern_quality = float(row.pattern_quality) if row and row.pattern_quality is not None else None
    buyability = row.buyability if row else None
    tier = _classify_tier(
        (row.pattern_type if row else None) or "",
        buyability or "",
        pattern_quality or 0.0,
    )

    target_r = None
    if suggest.target_price and suggest.stop_price and suggest.trigger_price:
        risk = suggest.trigger_price - suggest.stop_price
        if risk > 0:
            target_r = round((suggest.target_price - suggest.trigger_price) / risk, 2)

    # Empirical odds from the backtest signal cache
    if suggest.stop_price and suggest.target_price and suggest.atr14:
        odds = await compute_outcome_odds(
            session,
            pattern_type=row.pattern_type if row else None,
            buyability=buyability,
            pattern_quality=pattern_quality,
            entry_price=suggest.trigger_price,
            stop_price=suggest.stop_price,
            target_price=suggest.target_price,
            atr=suggest.atr14,
        )
    else:
        from app.services.odds_service import OddsEstimate
        odds = OddsEstimate(available=False, reason="Plan is incomplete (missing stop/target/ATR).")

    # Position size from the active trading account, when one is set
    sizing_out: TradePlanSizingOut | None = None
    active_id = await get_active_account_id(session, user_id)
    if active_id is not None and suggest.stop_price and suggest.trigger_price:
        currency = "CAD" if sym.endswith((".TO", ".NE", ".V")) else "USD"
        try:
            sizing, _streak, _bp = await preview_ticket(
                session,
                account_id=active_id,
                currency=currency,
                trigger_price=Decimal(str(suggest.trigger_price)),
                stop_price=Decimal(str(suggest.stop_price)),
                user_id=user_id,
            )
            sizing_out = TradePlanSizingOut(
                shares=sizing.shares,
                risk_amount=float(sizing.risk_amount),
                risk_pct=float(sizing.risk_pct),
                equity_basis=float(sizing.equity_basis),
                equity_currency=sizing.equity_currency,
                warnings=sizing.warnings,
            )
        except TicketValidationError:
            sizing_out = None

    return TradePlanOut(
        symbol=sym,
        pattern_type=row.pattern_type if row else None,
        pattern_quality=pattern_quality,
        buyability=buyability,
        tier=tier,
        pivot=suggest.trigger_price,
        stop=suggest.stop_price,
        stop_method=suggest.stop_method,
        target=suggest.target_price,
        target_r=target_r,
        last_close=suggest.last_close,
        atr14=suggest.atr14,
        sizing=sizing_out,
        odds=TradePlanOddsOut(**odds.__dict__),
    )


async def _fetch_on_demand(session: AsyncSession, symbol: str) -> None:
    """Auto-fetch 2yr of price data for any symbol not yet in the DB.
    Uses sync_eod_incremental which handles all the yfinance edge cases.
    """
    from app.services.eod_service import sync_eod_incremental
    log.info("On-demand price fetch for %s", symbol)
    try:
        await sync_eod_incremental(session, [symbol], full_years=2, delta_days=35)
        log.info("On-demand fetch complete for %s", symbol)
    except Exception:
        log.exception("On-demand fetch failed for %s", symbol)


@router.get("/{symbol}", response_model=ChartData)
async def chart(
    symbol: str,
    days: int = 504,
    session: AsyncSession = Depends(get_session),
) -> ChartData:
    symbol = symbol.upper()
    df = await get_bars_df(session, symbol, days=days)
    if df.empty:
        # Auto-fetch for any symbol not in the DB (e.g. TBIL, CASH.TO, any ticket symbol)
        await _fetch_on_demand(session, symbol)
        df = await get_bars_df(session, symbol, days=days)
    if df.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}. yfinance returned no data — check the ticker.")

    spy_df = await get_bars_df(session, "SPY", days=days)

    closes = df["close"].values.astype(float)
    dates  = [
        d.strftime("%Y-%m-%d") if hasattr(d, "strftime")
        else str(d)[:10]
        for d in df["date"].tolist()
    ]

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

    import asyncio
    loop = asyncio.get_event_loop()
    pivot, base_start = await loop.run_in_executor(
        None, _pattern_pivot, df, sma50_vals, sma200_vals
    )

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
