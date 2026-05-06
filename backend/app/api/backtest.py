"""Backtest API — walk-forward simulation of screener signals."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.backtest_service import BacktestResult, BacktestTrade, run_backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_running = False
_last_result: BacktestResult | None = None


class BacktestParams(BaseModel):
    tt_min:       int   = Field(default=6, ge=1, le=8)
    vcp_min:      float = Field(default=0.5, ge=0.0, le=1.0)
    stop_atr:     float = Field(default=1.5, ge=0.5, le=5.0)
    target_r:     float = Field(default=3.0, ge=1.0, le=10.0)
    time_stop:    int   = Field(default=20, ge=5, le=60)
    lookback_days: int  = Field(default=504, ge=126, le=504)
    symbols: list[str] | None = None


class TradeOut(BaseModel):
    symbol: str
    signal_date: str
    entry_date: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_date: str
    exit_price: float
    exit_reason: str
    r_multiple: float
    tt_score: int
    vcp_score: float
    bars_held: int


class BacktestOut(BaseModel):
    status: str   # "running" | "done" | "idle"
    params: BacktestParams | None = None
    total: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    total_r: float = 0.0
    max_drawdown_r: float = 0.0
    symbols_scanned: int = 0
    signals_found: int = 0
    equity_curve: list[dict] = []
    trades: list[TradeOut] = []


_last_params: BacktestParams | None = None


@router.post("/run", response_model=BacktestOut)
async def run(
    body: BacktestParams,
    background_tasks: BackgroundTasks,
) -> BacktestOut:
    global _running, _last_params
    if _running:
        return BacktestOut(status="running")
    _running = True
    _last_params = body
    background_tasks.add_task(_run_bg, body)
    return BacktestOut(status="running", params=body)


async def _run_bg(params: BacktestParams) -> None:
    global _running, _last_result
    from app.db.session import SessionLocal
    try:
        async with SessionLocal() as session:
            _last_result = await run_backtest(
                session,
                tt_min=params.tt_min,
                vcp_min=params.vcp_min,
                stop_atr=params.stop_atr,
                target_r=params.target_r,
                time_stop=params.time_stop,
                lookback_days=params.lookback_days,
                symbols=params.symbols,
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Backtest failed")
    finally:
        _running = False


@router.get("/status", response_model=BacktestOut)
async def status() -> BacktestOut:
    if _running:
        return BacktestOut(status="running", params=_last_params)
    if _last_result is None:
        return BacktestOut(status="idle")
    r = _last_result
    return BacktestOut(
        status="done",
        params=_last_params,
        total=r.total,
        wins=r.wins,
        losses=r.losses,
        scratches=r.scratches,
        win_rate=r.win_rate,
        avg_r=r.avg_r,
        avg_winner_r=r.avg_winner_r,
        avg_loser_r=r.avg_loser_r,
        expectancy=r.expectancy,
        profit_factor=r.profit_factor,
        total_r=r.total_r,
        max_drawdown_r=r.max_drawdown_r,
        symbols_scanned=r.symbols_scanned,
        signals_found=r.signals_found,
        equity_curve=r.equity_curve,
        trades=[TradeOut(**t.__dict__) for t in r.trades[:200]],  # cap for response size
    )
