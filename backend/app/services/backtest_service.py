"""Walk-forward backtest engine.

Scans historical daily bars for each symbol in the screener universe,
identifies sessions where TT ≥ threshold AND VCP ≥ threshold, simulates
entry at the next open, and tracks the outcome up to the exit criteria.

Exit criteria (in order of priority):
  1. Stop hit:   low of any subsequent bar ≤ stop_price → LOSS at stop_price
  2. Target hit: high of any subsequent bar ≥ target_price → WIN at target_price
  3. Time stop:  after time_stop_bars bars → SCRATCH at close

Parameters (all configurable in the request):
  tt_min       — minimum Trend Template score to enter (default 6)
  vcp_min      — minimum VCP score to enter (default 0.5)
  stop_atr     — stop = entry − stop_atr × ATR(14) (default 1.5)
  target_r     — target = entry + target_r × risk (default 3.0)
  time_stop    — exit after this many bars if neither stop nor target (default 20)
  base_risk_pct — risk per trade as fraction of equity (default 0.0075)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyBar, ScreenerSymbol
from app.services.eod_service import get_bars_df
from app.services.trend_template import MIN_BARS, score_trend_template
from app.services.vcp_scorer import score_vcp

log = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    symbol: str
    signal_date: str
    entry_date: str
    entry_price: float
    stop_price: float
    target_price: float
    exit_date: str
    exit_price: float
    exit_reason: str        # "stop" | "target" | "time"
    r_multiple: float
    tt_score: int
    vcp_score: float
    bars_held: int


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
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
    equity_curve: list[dict] = field(default_factory=list)   # [{date, cumulative_r}]
    symbols_scanned: int = 0
    signals_found: int = 0


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR for the last `period` bars."""
    if len(df) < period + 1:
        return float(df["close"].iloc[-1]) * 0.02  # fallback: 2% of price
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    trs = []
    for i in range(-period, 0):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
    return float(np.mean(trs))


async def run_backtest(
    session: AsyncSession,
    *,
    tt_min: int = 6,
    vcp_min: float = 0.5,
    stop_atr: float = 1.5,
    target_r: float = 3.0,
    time_stop: int = 20,
    lookback_days: int = 504,
    symbols: list[str] | None = None,
) -> BacktestResult:
    """Run a walk-forward backtest across the screener universe."""

    if symbols is None:
        result = await session.execute(
            select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
        )
        symbols = [r for (r,) in result.all()]

    # Load SPY for RS baseline
    spy_df = await get_bars_df(session, "SPY", days=lookback_days)

    result = BacktestResult(symbols_scanned=len(symbols))
    trades: list[BacktestTrade] = []

    for sym in symbols:
        df = await get_bars_df(session, sym, days=lookback_days)
        if df.empty or len(df) < MIN_BARS + time_stop + 5:
            continue

        closes  = df["close"].values.astype(float)
        highs   = df["high"].values.astype(float)
        lows    = df["low"].values.astype(float)
        opens   = df["open"].values.astype(float)
        dates   = df["date"].tolist()

        # Walk forward: for each bar from MIN_BARS to len-time_stop-2
        scan_end = len(df) - time_stop - 2
        for i in range(MIN_BARS, scan_end):
            hist = df.iloc[:i + 1]

            # Score at this point in history
            tt = score_trend_template(hist, benchmark_df=spy_df if not spy_df.empty else None)
            if tt.score < tt_min:
                continue

            vcp = score_vcp(hist, tt)
            if vcp.score < vcp_min:
                continue

            result.signals_found += 1

            # Entry: next bar's open
            entry_bar = i + 1
            entry_price = float(opens[entry_bar])
            if entry_price <= 0:
                continue

            # Stop and target
            atr = _atr(hist)
            stop_price   = entry_price - stop_atr * atr
            risk         = entry_price - stop_price
            if risk <= 0:
                continue
            target_price = entry_price + target_r * risk

            # Simulate forward
            exit_price  = float(closes[min(entry_bar + time_stop, len(df) - 1)])
            exit_reason = "time"
            exit_bar    = entry_bar + time_stop

            for j in range(entry_bar + 1, min(entry_bar + time_stop + 1, len(df))):
                if lows[j] <= stop_price:
                    exit_price  = stop_price
                    exit_reason = "stop"
                    exit_bar    = j
                    break
                if highs[j] >= target_price:
                    exit_price  = target_price
                    exit_reason = "target"
                    exit_bar    = j
                    break

            r_multiple = (exit_price - entry_price) / risk

            trades.append(BacktestTrade(
                symbol=sym,
                signal_date=str(dates[i])[:10],
                entry_date=str(dates[entry_bar])[:10],
                entry_price=round(entry_price, 2),
                stop_price=round(stop_price, 2),
                target_price=round(target_price, 2),
                exit_date=str(dates[min(exit_bar, len(dates) - 1)])[:10],
                exit_price=round(exit_price, 2),
                exit_reason=exit_reason,
                r_multiple=round(r_multiple, 3),
                tt_score=tt.score,
                vcp_score=round(float(vcp.score), 3),
                bars_held=exit_bar - entry_bar,
            ))

    # Aggregate stats
    result.trades = sorted(trades, key=lambda t: t.entry_date)
    result.total   = len(trades)
    if result.total == 0:
        return result

    wins     = [t for t in trades if t.r_multiple > 0.1]
    losses   = [t for t in trades if t.r_multiple < -0.05]
    scratches = [t for t in trades if t not in wins and t not in losses]

    result.wins      = len(wins)
    result.losses    = len(losses)
    result.scratches = len(scratches)
    result.win_rate  = round(len(wins) / result.total, 4)

    all_r = [t.r_multiple for t in trades]
    result.avg_r = round(float(np.mean(all_r)), 3)
    result.total_r = round(float(np.sum(all_r)), 3)
    result.avg_winner_r = round(float(np.mean([t.r_multiple for t in wins])), 3) if wins else 0.0
    result.avg_loser_r  = round(float(np.mean([t.r_multiple for t in losses])), 3) if losses else 0.0

    gross_wins  = sum(r for r in all_r if r > 0)
    gross_loss  = abs(sum(r for r in all_r if r < 0))
    result.profit_factor = round(gross_wins / gross_loss, 3) if gross_loss > 0 else float("inf")
    result.expectancy = round(
        result.win_rate * result.avg_winner_r + (1 - result.win_rate) * result.avg_loser_r, 3
    )

    # Equity curve + max drawdown
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    for t in result.trades:
        cumulative += t.r_multiple
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
        curve.append({"date": t.entry_date, "symbol": t.symbol, "r": t.r_multiple, "cumulative_r": round(cumulative, 3)})
    result.max_drawdown_r = round(max_dd, 3)
    result.equity_curve   = curve

    return result
