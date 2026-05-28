"""Backtest API — two-stage walk-forward simulation of screener signals.

Stage 1 (Trigger): does the stock reach its identified pivot within trigger_window?
Stage 2 (Outcome): if it triggers, does the resulting buy-stop trade win or lose?

Results are stratified by tier (S/A/B) and by pattern type so the user can
answer "what's the expected value of acting on a tier-S signal today?".
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from app.services.backtest_service import BacktestResult, run_backtest, simulate_from_candidates
from app.services.portfolio_sim_service import PortfolioResult, simulate_portfolio
from app.services.signal_scan_service import latest_successful_scan

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

_running = False
_last_result: BacktestResult | None = None
_last_params: "BacktestParams | None" = None
_last_scan_id: str | None = None
_last_used_cache: bool = False


class BacktestParams(BaseModel):
    tt_min:              int   = Field(default=4, ge=1, le=8)
    pattern_quality_min: float = Field(default=0.50, ge=0.0, le=1.0)
    stop_atr:            float = Field(default=1.5, ge=0.5, le=5.0)
    target_r:            float = Field(default=3.0, ge=1.0, le=10.0)
    time_stop:           int   = Field(default=20, ge=5, le=60)
    trigger_window:      int   = Field(default=30, ge=5, le=120)
    lookback_days:       int   = Field(default=504, ge=126, le=504)
    account_size:        float = Field(default=100_000.0, ge=1000.0, le=10_000_000.0)
    risk_pct:            float = Field(default=0.0075, ge=0.001, le=0.05)
    symbols: list[str] | None = None
    force_rescan:        bool  = Field(default=False, description="Skip the cached scan and re-detect signals from scratch (slow)")


class TradeOut(BaseModel):
    symbol: str
    signal_date: str
    pivot_price: float
    pattern_type: str
    pattern_quality: float
    buyability_at_signal: str
    tier: str
    tt_score: int
    vcp_score: float

    triggered: bool
    days_to_trigger: int | None = None
    entry_date: str | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    r_multiple: float | None = None
    dollar_pnl: float | None = None
    bars_held: int | None = None


class TierStatsOut(BaseModel):
    tier: str
    signals: int
    triggered: int
    trigger_rate: float
    avg_days_to_trigger: float
    target_hits: int
    stop_hits: int
    time_outs: int
    win_rate: float
    avg_r: float
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    win_loss_ratio: float = 0.0
    expectancy_per_signal_r: float
    total_r: float
    total_dollars: float


class PatternStatsOut(BaseModel):
    pattern_type: str
    signals: int
    triggered: int
    trigger_rate: float
    win_rate: float
    avg_r: float
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    win_loss_ratio: float = 0.0
    total_r: float
    total_dollars: float


class BacktestOut(BaseModel):
    status: str
    params: BacktestParams | None = None

    symbols_scanned: int = 0
    signals_found: int = 0
    signals_triggered: int = 0
    total_trades: int = 0

    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    win_loss_ratio: float = 0.0
    total_r: float = 0.0
    total_dollars: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_r: float = 0.0
    max_drawdown_dollars: float = 0.0

    by_tier: list[TierStatsOut] = []
    by_pattern: list[PatternStatsOut] = []
    equity_curve: list[dict] = []
    trades: list[TradeOut] = []

    benchmark_start_date: str | None = None
    benchmark_end_date: str | None = None
    benchmark_return_pct: float | None = None
    benchmark_dollars: float | None = None

    trades_per_month: float = 0.0
    signals_per_month: float = 0.0

    # Scan-cache info — lets the UI show "using cached scan from X" hint
    scan_id: str | None = None
    used_cached_scan: bool = False
    scan_finished_at: str | None = None
    scan_candidate_count: int = 0


@router.post("/run", response_model=BacktestOut)
async def run(body: BacktestParams, background_tasks: BackgroundTasks) -> BacktestOut:
    global _running, _last_params
    if _running:
        return BacktestOut(status="running")
    _running = True
    _last_params = body
    background_tasks.add_task(_run_bg, body)
    return BacktestOut(status="running", params=body)


async def _run_bg(params: BacktestParams) -> None:
    global _running, _last_result, _last_scan_id, _last_used_cache
    from app.db.session import SessionLocal
    try:
        async with SessionLocal() as session:
            outcome = await run_backtest(
                session,
                tt_min=params.tt_min,
                pattern_quality_min=params.pattern_quality_min,
                stop_atr=params.stop_atr,
                target_r=params.target_r,
                time_stop=params.time_stop,
                trigger_window=params.trigger_window,
                lookback_days=params.lookback_days,
                account_size=params.account_size,
                risk_pct=params.risk_pct,
                symbols=params.symbols,
                force_rescan=params.force_rescan,
            )
            _last_result = outcome.result
            _last_scan_id = str(outcome.scan_id)
            _last_used_cache = outcome.used_cached_scan
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
    # Pull scan metadata for the freshness hint
    scan_finished_str: str | None = None
    scan_candidate_count = 0
    if _last_scan_id:
        from app.db.session import SessionLocal
        from app.db.models import BacktestSignalScan
        import uuid as _uuid
        try:
            async with SessionLocal() as session:
                scan = await session.get(BacktestSignalScan, _uuid.UUID(_last_scan_id))
                if scan and scan.finished_at:
                    scan_finished_str = scan.finished_at.isoformat()
                    scan_candidate_count = scan.candidate_count
        except Exception:
            pass
    return BacktestOut(
        status="done",
        params=_last_params,
        symbols_scanned=r.symbols_scanned,
        signals_found=r.signals_found,
        signals_triggered=r.signals_triggered,
        total_trades=r.total_trades,
        wins=r.wins,
        losses=r.losses,
        scratches=r.scratches,
        win_rate=r.win_rate,
        avg_r=r.avg_r,
        avg_winner_r=r.avg_winner_r,
        avg_loser_r=r.avg_loser_r,
        win_loss_ratio=r.win_loss_ratio,
        total_r=r.total_r,
        total_dollars=r.total_dollars,
        profit_factor=r.profit_factor if r.profit_factor != float("inf") else 999.0,
        max_drawdown_r=r.max_drawdown_r,
        max_drawdown_dollars=r.max_drawdown_dollars,
        by_tier=[TierStatsOut(**t.__dict__) for t in r.by_tier],
        by_pattern=[PatternStatsOut(**p.__dict__) for p in r.by_pattern],
        equity_curve=r.equity_curve,
        trades=[TradeOut(**t.__dict__) for t in r.trades[:300]],
        benchmark_start_date=r.benchmark_start_date,
        benchmark_end_date=r.benchmark_end_date,
        benchmark_return_pct=r.benchmark_return_pct,
        benchmark_dollars=r.benchmark_dollars,
        trades_per_month=r.trades_per_month,
        signals_per_month=r.signals_per_month,
        scan_id=_last_scan_id,
        used_cached_scan=_last_used_cache,
        scan_finished_at=scan_finished_str,
        scan_candidate_count=scan_candidate_count,
    )


# ─── Parameter sweep ──────────────────────────────────────────────────────────


SWEEPABLE_PARAMS = {
    "stop_atr":            (0.5, 5.0),
    "target_r":            (1.0, 10.0),
    "time_stop":           (5, 60),
    "trigger_window":      (5, 120),
    "tt_min":              (1, 8),
    "pattern_quality_min": (0.0, 1.0),
}


class SweepRequest(BaseModel):
    base_params: BacktestParams = Field(description="Held constant for the sweep")
    sweep_param: str = Field(description="Name of the parameter to vary")
    sweep_values: list[float] = Field(min_length=2, max_length=20, description="Values to test")


class SweepRowOut(BaseModel):
    value: float
    signals_found: int
    signals_triggered: int
    total_trades: int
    win_rate: float                  # batting average
    avg_winner_r: float              # avg win as multiple of risk
    avg_loser_r: float               # avg loss (negative)
    win_loss_ratio: float            # avg win / |avg loss|
    avg_r: float                     # avg R per trade
    profit_factor: float
    total_r: float
    total_dollars: float
    max_drawdown_dollars: float


class SweepOut(BaseModel):
    sweep_param: str
    base_params: BacktestParams
    scan_id: str
    used_cached_scan: bool
    rows: list[SweepRowOut]


_sweep_running = False
_last_sweep: SweepOut | None = None


@router.post("/sweep", response_model=SweepOut)
async def sweep(
    body: SweepRequest,
    background_tasks: BackgroundTasks,
) -> SweepOut:
    """Run a parameter sweep — one base config, N different values for one param.

    Reuses the cached signal scan; only the cheap Phase-2 trade simulation runs
    per value. Returns a results row per value so the UI can show a side-by-side
    table.
    """
    global _sweep_running, _last_sweep
    if body.sweep_param not in SWEEPABLE_PARAMS:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"sweep_param must be one of {sorted(SWEEPABLE_PARAMS.keys())}",
        )
    lo, hi = SWEEPABLE_PARAMS[body.sweep_param]
    for v in body.sweep_values:
        if v < lo or v > hi:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"value {v} out of range [{lo}, {hi}] for {body.sweep_param}")

    if _sweep_running:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="A sweep is already running")
    _sweep_running = True
    background_tasks.add_task(_run_sweep_bg, body)
    # Return immediately with a placeholder; UI polls /sweep/status for the result.
    return SweepOut(
        sweep_param=body.sweep_param,
        base_params=body.base_params,
        scan_id="",
        used_cached_scan=False,
        rows=[],
    )


async def _run_sweep_bg(req: SweepRequest) -> None:
    global _sweep_running, _last_sweep
    from app.db.session import SessionLocal
    import logging
    try:
        async with SessionLocal() as session:
            # First, ensure we have a scan
            scan = await latest_successful_scan(session, lookback_days=req.base_params.lookback_days)
            if scan is None:
                # No cached scan yet — fall back to running a single backtest that
                # builds one. This is the slow path; user is warned in the UI.
                outcome = await run_backtest(
                    session,
                    tt_min=req.base_params.tt_min,
                    pattern_quality_min=req.base_params.pattern_quality_min,
                    stop_atr=req.base_params.stop_atr,
                    target_r=req.base_params.target_r,
                    time_stop=req.base_params.time_stop,
                    trigger_window=req.base_params.trigger_window,
                    lookback_days=req.base_params.lookback_days,
                    account_size=req.base_params.account_size,
                    risk_pct=req.base_params.risk_pct,
                    symbols=req.base_params.symbols,
                    force_rescan=False,
                )
                scan_id = outcome.scan_id
                used_cache = outcome.used_cached_scan
            else:
                scan_id = scan.id
                used_cache = True

            rows: list[SweepRowOut] = []
            for v in req.sweep_values:
                sim_kwargs = dict(
                    tt_min=req.base_params.tt_min,
                    pattern_quality_min=req.base_params.pattern_quality_min,
                    stop_atr=req.base_params.stop_atr,
                    target_r=req.base_params.target_r,
                    time_stop=req.base_params.time_stop,
                    trigger_window=req.base_params.trigger_window,
                    lookback_days=req.base_params.lookback_days,
                    account_size=req.base_params.account_size,
                    risk_pct=req.base_params.risk_pct,
                )
                # Overwrite the swept value (cast to int for integer params)
                if req.sweep_param in ("time_stop", "trigger_window", "tt_min"):
                    sim_kwargs[req.sweep_param] = int(v)
                else:
                    sim_kwargs[req.sweep_param] = float(v)

                res = await simulate_from_candidates(session, scan_id=scan_id, **sim_kwargs)
                rows.append(SweepRowOut(
                    value=v,
                    signals_found=res.signals_found,
                    signals_triggered=res.signals_triggered,
                    total_trades=res.total_trades,
                    win_rate=res.win_rate,
                    avg_winner_r=res.avg_winner_r,
                    avg_loser_r=res.avg_loser_r,
                    win_loss_ratio=res.win_loss_ratio,
                    avg_r=res.avg_r,
                    profit_factor=res.profit_factor if res.profit_factor != float("inf") else 999.0,
                    total_r=res.total_r,
                    total_dollars=res.total_dollars,
                    max_drawdown_dollars=res.max_drawdown_dollars,
                ))

            _last_sweep = SweepOut(
                sweep_param=req.sweep_param,
                base_params=req.base_params,
                scan_id=str(scan_id),
                used_cached_scan=used_cache,
                rows=rows,
            )
    except Exception:
        logging.getLogger(__name__).exception("Sweep failed")
    finally:
        _sweep_running = False


class SweepStatusOut(BaseModel):
    running: bool
    sweep: SweepOut | None


@router.get("/sweep/status", response_model=SweepStatusOut)
async def sweep_status() -> SweepStatusOut:
    return SweepStatusOut(running=_sweep_running, sweep=_last_sweep)


# ─── Portfolio simulator (Phase-2b: realistic capital constraints) ──────────


class PortfolioParams(BaseModel):
    """Same signal-detection params as BacktestParams, plus capital-management knobs."""
    tt_min:                  int   = Field(default=4, ge=1, le=8)
    pattern_quality_min:     float = Field(default=0.50, ge=0.0, le=1.0)
    stop_atr:                float = Field(default=1.5, ge=0.5, le=5.0)
    target_r:                float = Field(default=3.0, ge=1.0, le=10.0)
    time_stop:               int   = Field(default=20, ge=5, le=60)
    trigger_window:          int   = Field(default=30, ge=5, le=120)
    lookback_days:           int   = Field(default=504, ge=126, le=504)
    account_size:            float = Field(default=100_000.0, ge=1000.0, le=10_000_000.0)
    risk_pct:                float = Field(default=0.0075, ge=0.001, le=0.05)
    max_concurrent_positions: int  = Field(default=10, ge=1, le=50, description="Cap on simultaneous open positions")
    max_total_open_risk_pct: float = Field(default=0.08, ge=0.01, le=0.30, description="Cap on summed (entry-stop)/equity across open positions")
    cooldown_bars_after_exit: int  = Field(default=5, ge=0, le=60, description="Bars to wait after closing before re-entering the same symbol")


class PortfolioTradeOut(BaseModel):
    symbol: str
    tier: str
    pattern_type: str
    entry_date: str
    exit_date: str
    shares: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    r_multiple: float
    dollar_pnl: float
    exit_reason: str
    bars_held: int
    risk_dollars: float
    notional_at_entry: float


class EquityPointOut(BaseModel):
    date: str
    equity: float
    cash: float
    open_positions: int
    open_risk_dollars: float
    open_risk_pct: float


class PortfolioOut(BaseModel):
    status: str    # "idle" | "running" | "done"
    params: PortfolioParams | None = None

    initial_equity: float = 0
    final_equity: float = 0
    total_return_pct: float = 0
    cagr_pct: float = 0
    max_drawdown_pct: float = 0
    max_drawdown_dollars: float = 0
    time_in_market_pct: float = 0
    avg_concurrent_positions: float = 0
    max_concurrent_positions: int = 0

    total_signals_considered: int = 0
    total_signals_triggered: int = 0
    total_signals_taken: int = 0
    signal_acceptance_rate: float = 0
    rejected_capital: int = 0
    rejected_cooldown: int = 0
    rejected_already_open: int = 0

    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    avg_winner_r: float = 0
    avg_loser_r: float = 0
    win_loss_ratio: float = 0
    avg_r: float = 0
    profit_factor: float = 0

    benchmark_start_date: str | None = None
    benchmark_end_date: str | None = None
    benchmark_return_pct: float | None = None
    benchmark_dollars: float | None = None

    equity_curve: list[EquityPointOut] = []
    trades: list[PortfolioTradeOut] = []
    open_at_end: list[dict] = []

    scan_id: str | None = None
    used_cached_scan: bool = False


_portfolio_running = False
_last_portfolio: PortfolioResult | None = None
_last_portfolio_params: PortfolioParams | None = None
_last_portfolio_scan_id: str | None = None
_last_portfolio_used_cache: bool = False


@router.post("/portfolio", response_model=PortfolioOut)
async def run_portfolio(
    body: PortfolioParams,
    background_tasks: BackgroundTasks,
) -> PortfolioOut:
    """Kick off a realistic portfolio simulation. Reuses the cached signal scan."""
    global _portfolio_running, _last_portfolio_params
    if _portfolio_running:
        return PortfolioOut(status="running", params=_last_portfolio_params)
    _portfolio_running = True
    _last_portfolio_params = body
    background_tasks.add_task(_run_portfolio_bg, body)
    return PortfolioOut(status="running", params=body)


async def _run_portfolio_bg(params: PortfolioParams) -> None:
    global _portfolio_running, _last_portfolio, _last_portfolio_scan_id, _last_portfolio_used_cache
    from app.db.session import SessionLocal
    import logging
    try:
        async with SessionLocal() as session:
            # Reuse cached scan, or build one
            scan = await latest_successful_scan(session, lookback_days=params.lookback_days)
            if scan is None:
                # Slow path — falls back to running a single backtest first to build the cache
                outcome = await run_backtest(
                    session,
                    tt_min=params.tt_min,
                    pattern_quality_min=params.pattern_quality_min,
                    stop_atr=params.stop_atr,
                    target_r=params.target_r,
                    time_stop=params.time_stop,
                    trigger_window=params.trigger_window,
                    lookback_days=params.lookback_days,
                    account_size=params.account_size,
                    risk_pct=params.risk_pct,
                    force_rescan=False,
                )
                scan_id = outcome.scan_id
                used_cache = outcome.used_cached_scan
            else:
                scan_id = scan.id
                used_cache = True

            _last_portfolio = await simulate_portfolio(
                session,
                scan_id=scan_id,
                tt_min=params.tt_min,
                pattern_quality_min=params.pattern_quality_min,
                stop_atr=params.stop_atr,
                target_r=params.target_r,
                time_stop=params.time_stop,
                trigger_window=params.trigger_window,
                lookback_days=params.lookback_days,
                account_size=params.account_size,
                risk_pct=params.risk_pct,
                max_concurrent_positions=params.max_concurrent_positions,
                max_total_open_risk_pct=params.max_total_open_risk_pct,
                cooldown_bars_after_exit=params.cooldown_bars_after_exit,
            )
            _last_portfolio_scan_id = str(scan_id)
            _last_portfolio_used_cache = used_cache
    except Exception:
        logging.getLogger(__name__).exception("Portfolio sim failed")
    finally:
        _portfolio_running = False


@router.get("/portfolio/status", response_model=PortfolioOut)
async def portfolio_status() -> PortfolioOut:
    if _portfolio_running:
        return PortfolioOut(status="running", params=_last_portfolio_params)
    if _last_portfolio is None:
        return PortfolioOut(status="idle")
    r = _last_portfolio
    return PortfolioOut(
        status="done",
        params=_last_portfolio_params,
        initial_equity=r.initial_equity,
        final_equity=r.final_equity,
        total_return_pct=r.total_return_pct,
        cagr_pct=r.cagr_pct,
        max_drawdown_pct=r.max_drawdown_pct,
        max_drawdown_dollars=r.max_drawdown_dollars,
        time_in_market_pct=r.time_in_market_pct,
        avg_concurrent_positions=r.avg_concurrent_positions,
        max_concurrent_positions=r.max_concurrent_positions,
        total_signals_considered=r.total_signals_considered,
        total_signals_triggered=r.total_signals_triggered,
        total_signals_taken=r.total_signals_taken,
        signal_acceptance_rate=r.signal_acceptance_rate,
        rejected_capital=r.rejected_capital,
        rejected_cooldown=r.rejected_cooldown,
        rejected_already_open=r.rejected_already_open,
        closed_trades=r.closed_trades,
        wins=r.wins, losses=r.losses,
        win_rate=r.win_rate,
        avg_winner_r=r.avg_winner_r,
        avg_loser_r=r.avg_loser_r,
        win_loss_ratio=r.win_loss_ratio,
        avg_r=r.avg_r,
        profit_factor=r.profit_factor if r.profit_factor != float("inf") else 999.0,
        benchmark_start_date=r.benchmark_start_date,
        benchmark_end_date=r.benchmark_end_date,
        benchmark_return_pct=r.benchmark_return_pct,
        benchmark_dollars=r.benchmark_dollars,
        equity_curve=[EquityPointOut(**p.__dict__) for p in r.equity_curve],
        trades=[
            PortfolioTradeOut(
                symbol=t.symbol, tier=t.tier, pattern_type=t.pattern_type,
                entry_date=t.entry_date, exit_date=t.exit_date,
                shares=t.shares, entry_price=t.entry_price, exit_price=t.exit_price,
                stop_price=t.stop_price, target_price=t.target_price,
                r_multiple=t.r_multiple, dollar_pnl=t.dollar_pnl, exit_reason=t.exit_reason,
                bars_held=t.bars_held, risk_dollars=t.risk_dollars,
                notional_at_entry=t.notional_at_entry,
            )
            for t in r.trades[:500]
        ],
        open_at_end=r.open_at_end,
        scan_id=_last_portfolio_scan_id,
        used_cached_scan=_last_portfolio_used_cache,
    )
