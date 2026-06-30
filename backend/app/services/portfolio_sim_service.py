"""Realistic portfolio simulator — replays cached signals with capital constraints.

Unlike the signal-edge backtest (which treats every signal as independent and
sums R-multiples × constant dollars), this simulator maintains a running
portfolio: cash, open positions, total committed risk, current equity. Each
signal must compete for limited capital, sized off CURRENT equity (so winners
compound, losses shrink subsequent size).

This is the version that answers "what would $100k have actually become?"

Constraints enforced:
  • Max concurrent positions (default 10) — typical retail watch capacity
  • Max total open risk % (default 8%) — Minervini's rule
  • Cash availability — no margin in v1, so notional must fit in cash
  • Per-symbol uniqueness — only one open position per symbol at a time
  • Per-symbol cooldown — after closing a position, can't re-enter for N bars

Signal ranking when capital is the binding constraint:
  Tier S > Tier A > Tier B > unranked, then by pattern_quality desc.
  This matters because on a high-signal day, the portfolio can't take them all;
  ranking decides who gets in.

Outputs:
  • Equity curve in real $: every trading day, snapshot of equity/cash/positions
  • CAGR, max drawdown %, time in market %, signal acceptance rate
  • Trade log (closed trades only — open positions at end of window are marked
    as still-open and not counted in realized stats)

Limitations to know about (v1):
  • No trailing stops — initial stop stays until hit or time-stop fires
  • No partial exits / scale-outs
  • No commission/slippage modeling
  • SPY's US calendar is used as the canonical timeline; Canadian-only trading
    days are not separately walked (rare in practice)
  • Risk amount = (entry - stop) × shares is treated as static; doesn't decay
    as price moves away from stop
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BacktestSignalCandidate
from app.services.signal_scan_service import (
    ensure_bars_loaded,
    get_cached_bars,
    load_candidates,
)

log = logging.getLogger(__name__)


# ─── DTOs ────────────────────────────────────────────────────────────────────


@dataclass
class PortfolioPosition:
    symbol: str
    tier: str
    pattern_type: str
    shares: int
    entry_price: float
    stop_price: float
    target_price: float
    risk_dollars: float           # (entry - stop) × shares — static "initial risk"
    entry_date: date
    time_stop_bar: int            # exit-by bar in the symbol's series
    bar_index_at_entry: int       # for time-stop accounting
    notional_at_entry: float      # for cash bookkeeping


@dataclass
class PortfolioTrade:
    symbol: str
    tier: str
    pattern_type: str
    signal_date: str
    entry_date: str
    exit_date: str
    shares: int
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    r_multiple: float
    dollar_pnl: float
    exit_reason: str              # "stop" | "target" | "time" | "end_of_window"
    bars_held: int
    risk_dollars: float
    notional_at_entry: float
    equity_at_entry: float


@dataclass
class EquityPoint:
    date: str
    equity: float
    cash: float
    open_positions: int
    open_risk_dollars: float
    open_risk_pct: float


@dataclass
class PortfolioResult:
    initial_equity: float
    final_equity: float
    total_return_pct: float        # (final - initial) / initial
    cagr_pct: float                # annualized
    max_drawdown_pct: float
    max_drawdown_dollars: float
    time_in_market_pct: float      # % of trading days with at least one open position
    avg_concurrent_positions: float
    max_concurrent_positions: int

    total_signals_considered: int
    total_signals_triggered: int   # signals where pivot was actually hit
    total_signals_taken: int       # subset that the portfolio actually opened
    signal_acceptance_rate: float  # taken / triggered — measures how often capital was the bottleneck
    rejected_capital: int          # triggered but no room
    rejected_cooldown: int         # triggered but same-symbol cooldown
    rejected_already_open: int     # triggered but already in this symbol

    closed_trades: int
    wins: int
    losses: int
    win_rate: float                # batting average
    avg_winner_r: float
    avg_loser_r: float
    win_loss_ratio: float
    avg_r: float                   # mean R across closed trades
    profit_factor: float           # gross_wins_dollars / |gross_loss_dollars|

    # Benchmark
    benchmark_start_date: str | None = None
    benchmark_end_date: str | None = None
    benchmark_return_pct: float | None = None
    benchmark_dollars: float | None = None

    equity_curve: list[EquityPoint] = field(default_factory=list)
    trades: list[PortfolioTrade] = field(default_factory=list)
    open_at_end: list[dict] = field(default_factory=list)    # positions still open at sim end


# ─── Tier ranking ────────────────────────────────────────────────────────────


_TIER_RANK = {"S": 0, "A": 1, "B": 2, "": 3}


_TIER_S_PATTERNS_BACKTEST = ("bull_flag", "three_weeks_tight", "ascending_triangle", "high_tight_flag")


def _classify_tier(pattern_type: str, buyability: str, quality: float) -> str:
    """Same rules as backtest_service._classify_tier — duplicated to avoid
    circular import. Keep in sync.

    Same caveats as backtest_service: doesn't apply the live screener's
    RS-rank gate (no historical RS in cache) or accelerating-earnings filter.
    Backtest tier counts are upper-bound vs what live screener surfaces.
    """
    if buyability == "at_pivot":
        if pattern_type in _TIER_S_PATTERNS_BACKTEST and quality >= 0.50:
            return "S"
        if quality >= 0.60:
            return "A"
    elif buyability == "in_base":
        if quality >= 0.55:
            return "B"
    return ""


# ─── Core simulator ──────────────────────────────────────────────────────────


async def simulate_portfolio(
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
    max_concurrent_positions: int = 10,
    max_total_open_risk_pct: float = 0.08,
    cooldown_bars_after_exit: int = 5,
) -> PortfolioResult:
    """Walk the cached signals through a realistic portfolio simulation.

    Bar-by-bar across the universe using SPY's calendar. Each day:
      1. Process exits on open positions (stop/target/time hit on today's bar)
      2. Find candidates whose pivot is triggered today (high crosses pivot
         within the trigger_window from their signal_date)
      3. Sort triggers by tier then quality
      4. Try to open each in priority order, subject to capital constraints
      5. Snapshot equity
    """
    # Load candidates and filter by thresholds
    raw_candidates = await load_candidates(session, scan_id)
    if not raw_candidates:
        return _empty_result(account_size)

    candidates = [
        c for c in raw_candidates
        if c.tt_score >= tt_min and float(c.pattern_quality) >= pattern_quality_min
    ]
    if not candidates:
        return _empty_result(account_size)

    # Hydrate bars cache
    unique_symbols = sorted({c.symbol for c in candidates})
    await ensure_bars_loaded(session, unique_symbols, lookback_days)
    spy_df = get_cached_bars("SPY", lookback_days)

    # ─── Pre-compute trigger_day for each candidate ────────────────────────
    # For each candidate, walk forward up to trigger_window bars looking for
    # the first bar where high >= pivot. This is the day the buy-stop fills.
    # Cache the per-symbol bars numpy arrays for speed.
    bars_cache: dict[str, dict] = {}
    for sym in unique_symbols:
        df = get_cached_bars(sym, lookback_days)
        if df is None or df.empty:
            continue
        bars_cache[sym] = {
            "df": df,
            "highs": df["high"].values.astype(float),
            "lows":  df["low"].values.astype(float),
            "opens": df["open"].values.astype(float),
            "closes": df["close"].values.astype(float),
            "dates": [d if isinstance(d, date) else pd.Timestamp(d).date() for d in df["date"].tolist()],
        }

    @dataclass
    class _EnrichedCandidate:
        c: BacktestSignalCandidate
        tier: str
        trigger_bar: int | None     # bar index in symbol's series where pivot triggers; None if never
        trigger_date: date | None
        entry_price: float
        per_share_risk: float
        target_price: float

    enriched: list[_EnrichedCandidate] = []
    triggered_count = 0
    for c in candidates:
        if c.symbol not in bars_cache:
            continue
        bd = bars_cache[c.symbol]
        n = len(bd["highs"])
        i = c.bar_index
        if i >= n - 2:
            continue
        pivot = float(c.pivot_price)
        atr = float(c.atr_at_signal)
        per_share_risk = stop_atr * atr
        if per_share_risk <= 0:
            continue

        trigger_bar = None
        for j in range(i + 1, min(i + 1 + trigger_window, n)):
            if bd["highs"][j] >= pivot:
                trigger_bar = j
                break

        tier = _classify_tier(c.pattern_type, c.buyability, float(c.pattern_quality))
        if trigger_bar is None:
            enriched.append(_EnrichedCandidate(
                c=c, tier=tier, trigger_bar=None, trigger_date=None,
                entry_price=0.0, per_share_risk=per_share_risk, target_price=0.0,
            ))
            continue

        entry_price = max(float(bd["opens"][trigger_bar]), pivot)
        target_price = entry_price + target_r * per_share_risk
        enriched.append(_EnrichedCandidate(
            c=c, tier=tier, trigger_bar=trigger_bar, trigger_date=bd["dates"][trigger_bar],
            entry_price=entry_price, per_share_risk=per_share_risk, target_price=target_price,
        ))
        triggered_count += 1

    # Index triggered candidates by their trigger date for fast lookup during the day-walk
    triggers_by_date: dict[date, list[_EnrichedCandidate]] = defaultdict(list)
    for ec in enriched:
        if ec.trigger_date is not None:
            triggers_by_date[ec.trigger_date].append(ec)

    # ─── Build the canonical timeline (SPY's date series) ──────────────────
    if spy_df is None or spy_df.empty:
        return _empty_result(account_size)
    spy_dates: list[date] = [
        d if isinstance(d, date) else pd.Timestamp(d).date()
        for d in spy_df["date"].tolist()
    ]

    # Bound the simulation to the range of signal trigger dates (start a bit
    # earlier and end after the longest possible trade).
    if not triggers_by_date:
        return _empty_result(account_size)
    earliest_trigger = min(triggers_by_date.keys())
    latest_trigger = max(triggers_by_date.keys())
    sim_dates = [d for d in spy_dates if earliest_trigger <= d]
    # Allow trades opened near the end to play out
    extra_bars_after = time_stop + 5
    if len(sim_dates) > 0:
        last_idx_in_spy = spy_dates.index(sim_dates[-1])
        end_idx = min(len(spy_dates) - 1, last_idx_in_spy)  # extra bars handled by exit loop
    # (We just walk all sim_dates; exits naturally close near the end)

    # ─── Per-symbol cursor for fast bar lookup by date ──────────────────────
    sym_cursors: dict[str, int] = {sym: 0 for sym in bars_cache}

    def _bar_index_for_date(sym: str, d: date) -> int | None:
        """Return the index in sym's bar series whose date == d, or None."""
        bd = bars_cache.get(sym)
        if bd is None:
            return None
        dates = bd["dates"]
        cur = sym_cursors[sym]
        n = len(dates)
        # Advance cursor while behind
        while cur < n and dates[cur] < d:
            cur += 1
        sym_cursors[sym] = cur
        if cur < n and dates[cur] == d:
            return cur
        return None

    # ─── Portfolio state ────────────────────────────────────────────────────
    cash = float(account_size)
    open_positions: dict[str, PortfolioPosition] = {}
    closed_trades: list[PortfolioTrade] = []
    equity_curve: list[EquityPoint] = []
    cooldown_until_bar: dict[str, int] = {}    # symbol -> bar index in symbol's series until which we wait

    rejected_capital = 0
    rejected_cooldown = 0
    rejected_already_open = 0
    taken = 0
    max_concurrent = 0

    days_with_position = 0

    for d in sim_dates:
        # ── 1. Process exits on all open positions ──────────────────────────
        to_close: list[tuple[str, PortfolioPosition, float, str, int]] = []
        for sym, pos in open_positions.items():
            bi = _bar_index_for_date(sym, d)
            if bi is None:
                continue
            bd = bars_cache[sym]
            o = float(bd["opens"][bi])
            h = float(bd["highs"][bi])
            l = float(bd["lows"][bi])
            c = float(bd["closes"][bi])

            # Check gap-through on open first
            if o <= pos.stop_price:
                to_close.append((sym, pos, o, "stop", bi)); continue
            if o >= pos.target_price:
                to_close.append((sym, pos, o, "target", bi)); continue
            if l <= pos.stop_price:
                to_close.append((sym, pos, pos.stop_price, "stop", bi)); continue
            if h >= pos.target_price:
                to_close.append((sym, pos, pos.target_price, "target", bi)); continue
            # Time stop?
            if bi >= pos.time_stop_bar:
                to_close.append((sym, pos, c, "time", bi)); continue

        for sym, pos, exit_price, reason, exit_bar in to_close:
            cash += pos.shares * exit_price
            r_mult = (exit_price - pos.entry_price) / (pos.entry_price - pos.stop_price) if (pos.entry_price - pos.stop_price) > 0 else 0.0
            dollar_pnl = (exit_price - pos.entry_price) * pos.shares
            closed_trades.append(PortfolioTrade(
                symbol=pos.symbol, tier=pos.tier, pattern_type=pos.pattern_type,
                signal_date=str(pos.entry_date),
                entry_date=str(pos.entry_date),
                exit_date=str(d),
                shares=pos.shares,
                entry_price=round(pos.entry_price, 4),
                exit_price=round(exit_price, 4),
                stop_price=round(pos.stop_price, 4),
                target_price=round(pos.target_price, 4),
                r_multiple=round(r_mult, 3),
                dollar_pnl=round(dollar_pnl, 2),
                exit_reason=reason,
                bars_held=exit_bar - pos.bar_index_at_entry,
                risk_dollars=round(pos.risk_dollars, 2),
                notional_at_entry=round(pos.notional_at_entry, 2),
                equity_at_entry=0,   # filled in below
            ))
            del open_positions[sym]
            # Cooldown after exit — block re-entry for N bars
            cooldown_until_bar[sym] = exit_bar + cooldown_bars_after_exit

        # ── 2. Find candidates whose trigger is today, sort by tier ──────────
        todays_triggers = list(triggers_by_date.get(d, []))
        todays_triggers.sort(key=lambda ec: (_TIER_RANK.get(ec.tier, 99), -float(ec.c.pattern_quality)))

        # Compute current equity for sizing (cash + marked positions at today's close)
        marked_value = 0.0
        for sym, pos in open_positions.items():
            bi = _bar_index_for_date(sym, d)
            if bi is not None:
                marked_value += pos.shares * float(bars_cache[sym]["closes"][bi])
            else:
                # Stale mark — use last known close from entry. Rare.
                marked_value += pos.shares * pos.entry_price
        current_equity = cash + marked_value
        current_open_risk = sum(p.risk_dollars for p in open_positions.values())

        # ── 3. Try to open each in priority order ───────────────────────────
        for ec in todays_triggers:
            sym = ec.c.symbol
            if sym in open_positions:
                rejected_already_open += 1
                continue
            cooldown_until = cooldown_until_bar.get(sym)
            if cooldown_until is not None and ec.trigger_bar is not None and ec.trigger_bar < cooldown_until:
                rejected_cooldown += 1
                continue
            if len(open_positions) >= max_concurrent_positions:
                rejected_capital += 1
                continue

            # Risk budget check
            new_trade_risk_dollars = current_equity * risk_pct
            if (current_open_risk + new_trade_risk_dollars) > (current_equity * max_total_open_risk_pct):
                rejected_capital += 1
                continue

            # Position sizing
            shares = int(new_trade_risk_dollars / ec.per_share_risk)
            if shares <= 0:
                continue
            notional = shares * ec.entry_price
            if notional > cash:
                # Trim down to fit available cash
                shares = int(cash / ec.entry_price)
                if shares <= 0:
                    rejected_capital += 1
                    continue
                notional = shares * ec.entry_price
                # Recompute actual risk
                actual_risk_dollars = shares * ec.per_share_risk
            else:
                actual_risk_dollars = shares * ec.per_share_risk

            # Open the position
            entry_bar_index = ec.trigger_bar
            stop_price = ec.entry_price - ec.per_share_risk
            time_stop_bar = entry_bar_index + time_stop

            pos = PortfolioPosition(
                symbol=sym, tier=ec.tier, pattern_type=ec.c.pattern_type,
                shares=shares,
                entry_price=ec.entry_price,
                stop_price=stop_price,
                target_price=ec.target_price,
                risk_dollars=actual_risk_dollars,
                entry_date=d,
                time_stop_bar=time_stop_bar,
                bar_index_at_entry=entry_bar_index,
                notional_at_entry=notional,
            )
            open_positions[sym] = pos
            cash -= notional
            current_open_risk += actual_risk_dollars
            taken += 1
            # Note: not updating current_equity (already includes the notional)

            if len(open_positions) > max_concurrent:
                max_concurrent = len(open_positions)

        # ── 4. Snapshot equity ───────────────────────────────────────────────
        marked_value = 0.0
        for sym, pos in open_positions.items():
            bi = _bar_index_for_date(sym, d)
            if bi is not None:
                marked_value += pos.shares * float(bars_cache[sym]["closes"][bi])
            else:
                marked_value += pos.shares * pos.entry_price
        equity = cash + marked_value
        open_risk_pct = (current_open_risk / equity) if equity > 0 else 0.0

        equity_curve.append(EquityPoint(
            date=str(d),
            equity=round(equity, 2),
            cash=round(cash, 2),
            open_positions=len(open_positions),
            open_risk_dollars=round(current_open_risk, 2),
            open_risk_pct=round(open_risk_pct, 4),
        ))
        if open_positions:
            days_with_position += 1

    # ─── Wrap up: mark positions still open at end of window ────────────────
    open_at_end_marks: list[dict] = []
    final_marked = 0.0
    for sym, pos in open_positions.items():
        bd = bars_cache.get(sym)
        last_close = float(bd["closes"][-1]) if bd else pos.entry_price
        open_at_end_marks.append({
            "symbol": sym, "shares": pos.shares,
            "entry_price": round(pos.entry_price, 4),
            "current_price": round(last_close, 4),
            "unrealized_dollar_pnl": round((last_close - pos.entry_price) * pos.shares, 2),
            "entry_date": str(pos.entry_date),
            "tier": pos.tier,
            "pattern_type": pos.pattern_type,
        })
        final_marked += pos.shares * last_close
    final_equity = cash + final_marked

    # ─── Stats ─────────────────────────────────────────────────────────────
    n_closed = len(closed_trades)
    wins = [t for t in closed_trades if t.dollar_pnl > 0]
    losses = [t for t in closed_trades if t.dollar_pnl < 0]
    winner_rs = [t.r_multiple for t in wins]
    loser_rs = [t.r_multiple for t in losses]
    avg_winner_r = float(np.mean(winner_rs)) if winner_rs else 0.0
    avg_loser_r = float(np.mean(loser_rs)) if loser_rs else 0.0
    win_loss_ratio = abs(avg_winner_r / avg_loser_r) if avg_loser_r < 0 else 0.0
    avg_r = float(np.mean([t.r_multiple for t in closed_trades])) if closed_trades else 0.0
    gross_wins = sum(t.dollar_pnl for t in wins)
    gross_losses = abs(sum(t.dollar_pnl for t in losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)

    # CAGR
    if len(sim_dates) > 1 and account_size > 0:
        days_elapsed = (sim_dates[-1] - sim_dates[0]).days
        years = days_elapsed / 365.25 if days_elapsed > 0 else 0
        cagr = ((final_equity / account_size) ** (1 / years) - 1) * 100 if years > 0 else 0
    else:
        cagr = 0
    total_return_pct = ((final_equity - account_size) / account_size) * 100 if account_size > 0 else 0

    # Max drawdown
    peak = account_size
    max_dd_dollars = 0
    max_dd_pct = 0
    for pt in equity_curve:
        peak = max(peak, pt.equity)
        dd_d = peak - pt.equity
        if dd_d > max_dd_dollars:
            max_dd_dollars = dd_d
        dd_p = (dd_d / peak * 100) if peak > 0 else 0
        if dd_p > max_dd_pct:
            max_dd_pct = dd_p

    avg_concurrent = (sum(pt.open_positions for pt in equity_curve) / len(equity_curve)) if equity_curve else 0
    time_in_market = (days_with_position / len(equity_curve) * 100) if equity_curve else 0

    # SPY benchmark over the actual sim window
    bench_pct = None
    bench_dollars = None
    bench_start = None
    bench_end = None
    if spy_df is not None and not spy_df.empty and len(sim_dates) >= 2:
        first_d = sim_dates[0]
        last_d = sim_dates[-1]
        # Use SPY's close at those dates
        for d_test, c_test in zip(spy_dates, spy_df["close"].values.astype(float), strict=False):
            if d_test == first_d:
                spy_first = c_test
                break
        else:
            spy_first = None
        for d_test, c_test in zip(reversed(spy_dates), reversed(spy_df["close"].values.astype(float).tolist())):
            if d_test == last_d:
                spy_last = c_test
                break
        else:
            spy_last = None
        if spy_first and spy_last and spy_first > 0:
            bench_pct = round((spy_last - spy_first) / spy_first * 100, 2)
            bench_dollars = round(account_size * (spy_last - spy_first) / spy_first, 2)
            bench_start = str(first_d)
            bench_end = str(last_d)

    return PortfolioResult(
        initial_equity=account_size,
        final_equity=round(final_equity, 2),
        total_return_pct=round(total_return_pct, 2),
        cagr_pct=round(cagr, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        max_drawdown_dollars=round(max_dd_dollars, 2),
        time_in_market_pct=round(time_in_market, 1),
        avg_concurrent_positions=round(avg_concurrent, 2),
        max_concurrent_positions=max_concurrent,

        total_signals_considered=len(candidates),
        total_signals_triggered=triggered_count,
        total_signals_taken=taken,
        signal_acceptance_rate=round(taken / triggered_count, 4) if triggered_count > 0 else 0.0,
        rejected_capital=rejected_capital,
        rejected_cooldown=rejected_cooldown,
        rejected_already_open=rejected_already_open,

        closed_trades=n_closed,
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / n_closed, 4) if n_closed > 0 else 0.0,
        avg_winner_r=round(avg_winner_r, 3),
        avg_loser_r=round(avg_loser_r, 3),
        win_loss_ratio=round(win_loss_ratio, 3),
        avg_r=round(avg_r, 3),
        profit_factor=round(profit_factor, 3),

        benchmark_start_date=bench_start,
        benchmark_end_date=bench_end,
        benchmark_return_pct=bench_pct,
        benchmark_dollars=bench_dollars,

        equity_curve=equity_curve,
        trades=closed_trades,
        open_at_end=open_at_end_marks,
    )


def _empty_result(account_size: float) -> PortfolioResult:
    return PortfolioResult(
        initial_equity=account_size,
        final_equity=account_size,
        total_return_pct=0.0,
        cagr_pct=0.0,
        max_drawdown_pct=0.0,
        max_drawdown_dollars=0.0,
        time_in_market_pct=0.0,
        avg_concurrent_positions=0.0,
        max_concurrent_positions=0,
        total_signals_considered=0,
        total_signals_triggered=0,
        total_signals_taken=0,
        signal_acceptance_rate=0.0,
        rejected_capital=0,
        rejected_cooldown=0,
        rejected_already_open=0,
        closed_trades=0,
        wins=0, losses=0,
        win_rate=0.0,
        avg_winner_r=0.0, avg_loser_r=0.0,
        win_loss_ratio=0.0,
        avg_r=0.0,
        profit_factor=0.0,
    )
