"""Two-stage walk-forward backtest.

For every historical day a stock would have shown up in the screener with a
defined pivot, we record a *signal*. Each signal then runs through two stages:

  Stage 1 — Trigger:
    Watch the next `trigger_window` bars. Did `high >= pivot_price`?
    If yes, that's a fill at the buy-stop (entry = pivot, with gap-up handling).
    If no, the signal expires without ever becoming a trade.

  Stage 2 — Outcome (only if triggered):
    Same as before: stop = entry - stop_atr*ATR, target = entry + target_r*risk,
    walk forward to first stop/target touch or time-stop close.

This decomposition matches how a real trader uses the screener: place a buy-stop
at the pivot, only get filled on actual breakouts. The two-stage stats let you
ask the right questions — "of the S-tier signals I'd see on any given day, what
fraction actually triggers, and what's the EV when they do?"

Signals are stratified by tier (S/A/B, matching screener.todays_picks) and by
pattern type, so the UI can show "average outcome of an S-tier signal" vs "of
a CWH" etc.

Per-symbol cooldown: once a signal is recorded for a symbol, suppress new
signals for that symbol until the open trade closes (or the trigger window
expires without a fill). Prevents the same setup firing 5-20× as scores stay
above threshold across consecutive bars.

Tier rules here intentionally drop the "accelerating earnings" filter that
screener.py applies to Tier A — we don't have historical earnings snapshots.
The UI calls this out so the user understands Tier A backtest results are
optimistic vs. live screener.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BacktestSignalCandidate, BacktestSignalScan, DailyBar, ScreenerSymbol
from app.services.eod_service import get_bars_df
from app.services.signal_scan_service import (
    ensure_bars_loaded,
    get_cached_bars,
    latest_successful_scan,
    load_candidates,
    scan_universe,
)
from app.services.trend_template import MIN_BARS

log = logging.getLogger(__name__)


Tier = Literal["S", "A", "B", ""]


@dataclass
class BacktestTrade:
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
    days_to_trigger: int | None       # bars from signal to first pivot touch; None if expired
    entry_date: str | None
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    exit_date: str | None
    exit_price: float | None
    exit_reason: str | None           # "stop" | "target" | "time" | None if not triggered
    r_multiple: float | None
    dollar_pnl: float | None
    bars_held: int | None


@dataclass
class TierStats:
    tier: str                     # "S" | "A" | "B" | "All"
    signals: int = 0
    triggered: int = 0
    trigger_rate: float = 0.0
    avg_days_to_trigger: float = 0.0
    target_hits: int = 0
    stop_hits: int = 0
    time_outs: int = 0
    win_rate: float = 0.0                  # batting average over triggered trades
    avg_r: float = 0.0                     # mean R across all triggered (winners+losers+scratches)
    avg_winner_r: float = 0.0              # mean R of winning trades only
    avg_loser_r: float = 0.0               # mean R of losing trades only (negative)
    win_loss_ratio: float = 0.0            # |avg_winner_r / avg_loser_r| — Minervini's reward:risk
    expectancy_per_signal_r: float = 0.0   # avg R per signal, including non-triggers
    total_r: float = 0.0
    total_dollars: float = 0.0


@dataclass
class PatternStats:
    pattern_type: str
    signals: int = 0
    triggered: int = 0
    trigger_rate: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    win_loss_ratio: float = 0.0
    total_r: float = 0.0
    total_dollars: float = 0.0


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    symbols_scanned: int = 0
    signals_found: int = 0
    signals_triggered: int = 0
    total_trades: int = 0

    # Overall stats (across triggered trades only)
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0     # mean R of winning trades only (Minervini's "avg gain")
    avg_loser_r: float = 0.0      # mean R of losing trades only (Minervini's "avg loss")
    win_loss_ratio: float = 0.0   # |avg_winner_r / avg_loser_r|
    total_r: float = 0.0
    total_dollars: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_r: float = 0.0
    max_drawdown_dollars: float = 0.0

    # Stratified
    by_tier: list[TierStats] = field(default_factory=list)
    by_pattern: list[PatternStats] = field(default_factory=list)

    # Equity curve
    equity_curve: list[dict] = field(default_factory=list)

    # Benchmark
    benchmark_start_date: str | None = None
    benchmark_end_date: str | None = None
    benchmark_return_pct: float | None = None   # SPY buy-and-hold over same window
    benchmark_dollars: float | None = None       # what $account_size would have become in SPY

    # Frequency
    trades_per_month: float = 0.0
    signals_per_month: float = 0.0


# ─── Tier classification ──────────────────────────────────────────────────────
#
# Mirror of api/screener.py:todays_picks tier rules, with two unavoidable
# differences vs the live screener:
#   1. RS-rank gate (S≥85, A≥75, B≥70) is NOT applied — the cached signal
#      candidates don't store historical RS rank, and recomputing it per
#      (symbol, bar) would require ranking the whole universe at every
#      historical date. So backtest tier counts will be LARGER than what
#      the live screener would actually surface.
#   2. Accelerating-earnings filter for Tier A is NOT applied — no
#      historical earnings snapshots in DB.
#
# The pattern-set and quality thresholds are applied identically. So this
# backtest measures the edge of the *pattern definitions*, not the full
# live-screener funnel. RS-gate impact has to be evaluated live.

_TIER_S_PATTERNS_BACKTEST = ("bull_flag", "three_weeks_tight", "ascending_triangle", "high_tight_flag")


def _classify_tier(pattern_type: str, buyability: str, quality: float) -> Tier:
    if buyability == "at_pivot":
        if pattern_type in _TIER_S_PATTERNS_BACKTEST and quality >= 0.50:
            return "S"
        if quality >= 0.60:
            return "A"
    elif buyability == "in_base":
        if quality >= 0.55:
            return "B"
    return ""


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float(df["close"].iloc[-1]) * 0.02
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    trs = []
    for i in range(-period, 0):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
    return float(np.mean(trs))


def _ma(closes: np.ndarray, n: int) -> float | None:
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


# ─── Public entry point (Phase 1 + Phase 2 orchestrator) ────────────────────


@dataclass
class RunBacktestOutcome:
    """Returned by run_backtest. Includes the scan_id that was used so the
    caller (sweep endpoint, UI) can reuse it for follow-up simulations."""
    result: BacktestResult
    scan_id: uuid.UUID
    used_cached_scan: bool


async def run_backtest(
    session: AsyncSession,
    *,
    tt_min: int = 4,
    pattern_quality_min: float = 0.50,
    stop_atr: float = 1.5,
    target_r: float = 3.0,
    time_stop: int = 20,
    trigger_window: int = 30,
    lookback_days: int = 504,
    account_size: float = 100_000.0,
    risk_pct: float = 0.0075,
    symbols: list[str] | None = None,
    force_rescan: bool = False,
    progress_callback=None,
) -> RunBacktestOutcome:
    """Walk-forward backtest. Reuses a cached scan when `lookback_days` matches,
    unless `force_rescan=True`. Scan-heavy work runs only when needed; trade
    simulation is fast.

    Returns RunBacktestOutcome (BacktestResult + scan_id + cache hit flag).
    """
    scan: BacktestSignalScan | None = None  # type: ignore[name-defined]
    if not force_rescan:
        scan = await latest_successful_scan(session, lookback_days=lookback_days)

    if scan is None:
        scan_id = await scan_universe(
            session, lookback_days=lookback_days, symbols=symbols,
            progress_callback=progress_callback,
        )
        used_cache = False
    else:
        scan_id = scan.id
        used_cache = True

    result = await simulate_from_candidates(
        session,
        scan_id=scan_id,
        tt_min=tt_min,
        pattern_quality_min=pattern_quality_min,
        stop_atr=stop_atr,
        target_r=target_r,
        time_stop=time_stop,
        trigger_window=trigger_window,
        lookback_days=lookback_days,
        account_size=account_size,
        risk_pct=risk_pct,
    )
    return RunBacktestOutcome(result=result, scan_id=scan_id, used_cached_scan=used_cache)


# ─── Phase 2: trade simulation from cached candidates ────────────────────────


async def simulate_from_candidates(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    tt_min: int = 4,
    pattern_quality_min: float = 0.50,
    stop_atr: float = 1.5,
    target_r: float = 3.0,
    time_stop: int = 20,
    trigger_window: int = 30,
    lookback_days: int = 504,
    account_size: float = 100_000.0,
    risk_pct: float = 0.0075,
) -> BacktestResult:
    """Fast Phase-2 simulation.

    Loads cached candidates from `scan_id`, filters by thresholds, and walks
    each forward through the trigger window + stop/target/time-stop simulation.
    Reuses in-process bars cache populated by Phase 1.

    Typical runtime: ~30s for the full ~7k-symbol universe.
    """
    candidates = await load_candidates(session, scan_id)
    if not candidates:
        return BacktestResult(symbols_scanned=0)

    # Hydrate bars cache if Phase 1 ran in a different process
    unique_symbols = sorted({c.symbol for c in candidates})
    await ensure_bars_loaded(session, unique_symbols, lookback_days)

    dollars_per_trade = account_size * risk_pct
    result = BacktestResult(symbols_scanned=len(unique_symbols))
    trades: list[BacktestTrade] = []
    earliest_date: str | None = None
    latest_date: str | None = None

    # Group candidates by symbol so we can enforce per-symbol cooldown
    by_symbol: dict[str, list[BacktestSignalCandidate]] = {}
    for c in candidates:
        by_symbol.setdefault(c.symbol, []).append(c)

    for sym, sym_candidates in by_symbol.items():
        df = get_cached_bars(sym, lookback_days)
        if df is None or df.empty:
            continue

        closes = df["close"].values.astype(float)
        highs  = df["high"].values.astype(float)
        lows   = df["low"].values.astype(float)
        opens  = df["open"].values.astype(float)
        dates  = df["date"].tolist()
        n_bars = len(df)

        next_eligible = 0
        # Candidates are stored in scan order (sorted by bar_index already, but
        # be defensive)
        sym_candidates.sort(key=lambda c: c.bar_index)

        for c in sym_candidates:
            i = c.bar_index
            if i < next_eligible:
                continue
            # Apply post-hoc threshold filters
            if c.tt_score < tt_min:
                continue
            if float(c.pattern_quality) < pattern_quality_min:
                continue

            tier = _classify_tier(c.pattern_type, c.buyability, float(c.pattern_quality))
            pivot = float(c.pivot_price)
            signal_date = str(c.signal_date)[:10]

            if earliest_date is None or signal_date < earliest_date:
                earliest_date = signal_date
            if latest_date is None or signal_date > latest_date:
                latest_date = signal_date

            # Stage 1: trigger watch
            triggered = False
            trigger_bar = None
            for j in range(i + 1, min(i + 1 + trigger_window, n_bars)):
                if highs[j] >= pivot:
                    triggered = True
                    trigger_bar = j
                    break

            if not triggered:
                trades.append(BacktestTrade(
                    symbol=sym, signal_date=signal_date,
                    pivot_price=round(pivot, 2),
                    pattern_type=c.pattern_type,
                    pattern_quality=round(float(c.pattern_quality), 3),
                    buyability_at_signal=c.buyability,
                    tier=tier,
                    tt_score=c.tt_score,
                    vcp_score=round(float(c.vcp_score), 3),
                    triggered=False,
                    days_to_trigger=None,
                    entry_date=None, entry_price=None,
                    stop_price=None, target_price=None,
                    exit_date=None, exit_price=None, exit_reason=None,
                    r_multiple=None, dollar_pnl=None, bars_held=None,
                ))
                next_eligible = i + trigger_window + 1
                continue

            # Stage 2: simulate the trade
            entry_price = max(float(opens[trigger_bar]), pivot)
            atr = float(c.atr_at_signal)
            stop_price = entry_price - stop_atr * atr
            risk = entry_price - stop_price
            if risk <= 0:
                next_eligible = trigger_bar + 1
                continue
            target_price = entry_price + target_r * risk

            exit_bar = min(trigger_bar + time_stop, n_bars - 1)
            exit_price = float(closes[exit_bar])
            exit_reason = "time"

            for k in range(trigger_bar + 1, min(trigger_bar + time_stop + 1, n_bars)):
                bar_open = float(opens[k])
                if bar_open <= stop_price:
                    exit_price = bar_open; exit_reason = "stop"; exit_bar = k; break
                if bar_open >= target_price:
                    exit_price = bar_open; exit_reason = "target"; exit_bar = k; break
                if lows[k] <= stop_price:
                    exit_price = stop_price; exit_reason = "stop"; exit_bar = k; break
                if highs[k] >= target_price:
                    exit_price = target_price; exit_reason = "target"; exit_bar = k; break

            r_multiple = (exit_price - entry_price) / risk
            dollar_pnl = r_multiple * dollars_per_trade

            trades.append(BacktestTrade(
                symbol=sym,
                signal_date=signal_date,
                pivot_price=round(pivot, 2),
                pattern_type=c.pattern_type,
                pattern_quality=round(float(c.pattern_quality), 3),
                buyability_at_signal=c.buyability,
                tier=tier,
                tt_score=c.tt_score,
                vcp_score=round(float(c.vcp_score), 3),
                triggered=True,
                days_to_trigger=trigger_bar - i,
                entry_date=str(dates[trigger_bar])[:10],
                entry_price=round(entry_price, 2),
                stop_price=round(stop_price, 2),
                target_price=round(target_price, 2),
                exit_date=str(dates[exit_bar])[:10],
                exit_price=round(exit_price, 2),
                exit_reason=exit_reason,
                r_multiple=round(r_multiple, 3),
                dollar_pnl=round(dollar_pnl, 2),
                bars_held=exit_bar - trigger_bar,
            ))

            next_eligible = exit_bar + 1

    # Aggregate
    result.trades = sorted(trades, key=lambda t: t.signal_date)
    result.signals_found = len(trades)
    triggered_trades = [t for t in trades if t.triggered]
    result.signals_triggered = len(triggered_trades)
    result.total_trades = len(triggered_trades)

    if triggered_trades:
        all_r = [t.r_multiple for t in triggered_trades if t.r_multiple is not None]
        wins = [t for t in triggered_trades if (t.r_multiple or 0) > 0.1]
        losses = [t for t in triggered_trades if (t.r_multiple or 0) < -0.05]
        scratches = [t for t in triggered_trades if t not in wins and t not in losses]
        result.wins = len(wins)
        result.losses = len(losses)
        result.scratches = len(scratches)
        result.win_rate = round(len(wins) / len(triggered_trades), 4)
        result.avg_r = round(float(np.mean(all_r)), 3) if all_r else 0.0
        result.total_r = round(float(np.sum(all_r)), 3) if all_r else 0.0
        result.total_dollars = round(sum(t.dollar_pnl or 0 for t in triggered_trades), 2)

        winner_rs = [t.r_multiple for t in wins if t.r_multiple is not None]
        loser_rs  = [t.r_multiple for t in losses if t.r_multiple is not None]
        result.avg_winner_r = round(float(np.mean(winner_rs)), 3) if winner_rs else 0.0
        result.avg_loser_r  = round(float(np.mean(loser_rs)), 3)  if loser_rs  else 0.0
        if result.avg_loser_r < 0:
            result.win_loss_ratio = round(abs(result.avg_winner_r / result.avg_loser_r), 3)

        gross_wins = sum(r for r in all_r if r > 0)
        gross_loss = abs(sum(r for r in all_r if r < 0))
        result.profit_factor = round(gross_wins / gross_loss, 3) if gross_loss > 0 else float("inf")

        # Equity curve in dollars
        cumulative = 0.0
        cumulative_r = 0.0
        peak_r = 0.0
        peak_dollars = 0.0
        max_dd_r = 0.0
        max_dd_dollars = 0.0
        curve = []
        sorted_triggered = sorted(triggered_trades, key=lambda t: t.entry_date or "")
        for t in sorted_triggered:
            cumulative_r += t.r_multiple or 0
            cumulative += t.dollar_pnl or 0
            peak_r = max(peak_r, cumulative_r)
            peak_dollars = max(peak_dollars, cumulative)
            max_dd_r = max(max_dd_r, peak_r - cumulative_r)
            max_dd_dollars = max(max_dd_dollars, peak_dollars - cumulative)
            curve.append({
                "date": t.entry_date,
                "symbol": t.symbol,
                "r": round(t.r_multiple or 0, 3),
                "cumulative_r": round(cumulative_r, 3),
                "cumulative_dollars": round(cumulative, 2),
            })
        result.max_drawdown_r = round(max_dd_r, 3)
        result.max_drawdown_dollars = round(max_dd_dollars, 2)
        result.equity_curve = curve

    # Per-tier and per-pattern stratification
    result.by_tier = _stratify_by_tier(trades, dollars_per_trade)
    result.by_pattern = _stratify_by_pattern(trades, dollars_per_trade)

    # SPY benchmark over the actual signal window
    spy_df = get_cached_bars("SPY", lookback_days)
    if earliest_date and latest_date and spy_df is not None and not spy_df.empty:
        spy_df = spy_df.copy()
        spy_df["date_str"] = spy_df["date"].astype(str).str[:10]
        in_window = spy_df[(spy_df["date_str"] >= earliest_date) & (spy_df["date_str"] <= latest_date)]
        if len(in_window) >= 2:
            spy_start = float(in_window["close"].iloc[0])
            spy_end = float(in_window["close"].iloc[-1])
            if spy_start > 0:
                bench_return = (spy_end - spy_start) / spy_start
                result.benchmark_start_date = str(in_window["date"].iloc[0])[:10]
                result.benchmark_end_date = str(in_window["date"].iloc[-1])[:10]
                result.benchmark_return_pct = round(bench_return * 100, 2)
                result.benchmark_dollars = round(account_size * bench_return, 2)

    # Frequency: how often signals fire per month
    if earliest_date and latest_date:
        try:
            d0 = datetime.strptime(earliest_date, "%Y-%m-%d")
            d1 = datetime.strptime(latest_date, "%Y-%m-%d")
            months = max((d1 - d0).days / 30.0, 1.0 / 30.0)
            result.signals_per_month = round(len(trades) / months, 2)
            result.trades_per_month = round(len(triggered_trades) / months, 2)
        except Exception:
            pass

    return result


def _stratify_by_tier(trades: list[BacktestTrade], dollars_per_trade: float) -> list[TierStats]:
    out: list[TierStats] = []
    for tier in ("S", "A", "B", "All"):
        if tier == "All":
            subset = trades
        else:
            subset = [t for t in trades if t.tier == tier]
        if not subset:
            out.append(TierStats(tier=tier))
            continue
        triggered = [t for t in subset if t.triggered]
        wins = [t for t in triggered if (t.r_multiple or 0) > 0.1]
        losses = [t for t in triggered if (t.r_multiple or 0) < -0.05]
        r_values = [t.r_multiple for t in triggered if t.r_multiple is not None]
        winner_rs = [t.r_multiple for t in wins if t.r_multiple is not None]
        loser_rs  = [t.r_multiple for t in losses if t.r_multiple is not None]
        dollars = sum(t.dollar_pnl or 0 for t in triggered)
        avg_days = (
            float(np.mean([t.days_to_trigger for t in triggered if t.days_to_trigger is not None]))
            if triggered else 0.0
        )
        avg_r = float(np.mean(r_values)) if r_values else 0.0
        avg_winner_r = float(np.mean(winner_rs)) if winner_rs else 0.0
        avg_loser_r  = float(np.mean(loser_rs))  if loser_rs  else 0.0
        win_loss_ratio = abs(avg_winner_r / avg_loser_r) if avg_loser_r < 0 else 0.0
        total_r = float(np.sum(r_values)) if r_values else 0.0
        ev_per_signal = total_r / len(subset) if subset else 0.0

        out.append(TierStats(
            tier=tier,
            signals=len(subset),
            triggered=len(triggered),
            trigger_rate=round(len(triggered) / len(subset), 4) if subset else 0.0,
            avg_days_to_trigger=round(avg_days, 1),
            target_hits=sum(1 for t in triggered if t.exit_reason == "target"),
            stop_hits=sum(1 for t in triggered if t.exit_reason == "stop"),
            time_outs=sum(1 for t in triggered if t.exit_reason == "time"),
            win_rate=round(len(wins) / len(triggered), 4) if triggered else 0.0,
            avg_r=round(avg_r, 3),
            avg_winner_r=round(avg_winner_r, 3),
            avg_loser_r=round(avg_loser_r, 3),
            win_loss_ratio=round(win_loss_ratio, 3),
            expectancy_per_signal_r=round(ev_per_signal, 3),
            total_r=round(total_r, 3),
            total_dollars=round(dollars, 2),
        ))
    return out


def _stratify_by_pattern(trades: list[BacktestTrade], dollars_per_trade: float) -> list[PatternStats]:
    by_pat: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        by_pat.setdefault(t.pattern_type, []).append(t)
    out: list[PatternStats] = []
    for pat, subset in by_pat.items():
        triggered = [t for t in subset if t.triggered]
        wins = [t for t in triggered if (t.r_multiple or 0) > 0.1]
        losses = [t for t in triggered if (t.r_multiple or 0) < -0.05]
        r_values = [t.r_multiple for t in triggered if t.r_multiple is not None]
        winner_rs = [t.r_multiple for t in wins if t.r_multiple is not None]
        loser_rs  = [t.r_multiple for t in losses if t.r_multiple is not None]
        dollars = sum(t.dollar_pnl or 0 for t in triggered)
        avg_r = float(np.mean(r_values)) if r_values else 0.0
        avg_winner_r = float(np.mean(winner_rs)) if winner_rs else 0.0
        avg_loser_r  = float(np.mean(loser_rs))  if loser_rs  else 0.0
        win_loss_ratio = abs(avg_winner_r / avg_loser_r) if avg_loser_r < 0 else 0.0
        total_r = float(np.sum(r_values)) if r_values else 0.0
        out.append(PatternStats(
            pattern_type=pat,
            signals=len(subset),
            triggered=len(triggered),
            trigger_rate=round(len(triggered) / len(subset), 4) if subset else 0.0,
            win_rate=round(len(wins) / len(triggered), 4) if triggered else 0.0,
            avg_r=round(avg_r, 3),
            avg_winner_r=round(avg_winner_r, 3),
            avg_loser_r=round(avg_loser_r, 3),
            win_loss_ratio=round(win_loss_ratio, 3),
            total_r=round(total_r, 3),
            total_dollars=round(dollars, 2),
        ))
    out.sort(key=lambda p: p.signals, reverse=True)
    return out
