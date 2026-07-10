"""Outcome labels for the ML setup ranker.

Replays every cached Phase-1 candidate through the shared trade simulation
(simulate_candidate — the same entry/exit conventions as the Phase-2 backtest
and the odds card) and records the realistic EOD-workflow outcome: next-day-
onward buy-stop fill, stop/target/time-stop exit, MAE/MFE, forward returns.

No threshold filters are applied — the model should learn from the full
candidate distribution the scan produces, not a pre-filtered slice.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BacktestSignalScan
from app.services.backtest_service import simulate_candidate
from app.services.signal_scan_service import (
    ensure_bars_loaded,
    get_cached_bars,
    load_candidates,
)

log = logging.getLogger(__name__)


@dataclass
class LabeledSetup:
    candidate_id: uuid.UUID
    symbol: str
    signal_date: str                  # YYYY-MM-DD
    features: dict | None

    triggered: bool
    censored: bool
    days_to_trigger: int | None
    exit_reason: str | None           # stop | target | time
    r_multiple: float | None
    mae_r: float | None
    mfe_r: float | None
    mae_pct: float | None
    mfe_pct: float | None
    fwd_ret_5: float | None           # (close[entry_bar+n] − entry) / entry * 100
    fwd_ret_10: float | None
    fwd_ret_20: float | None


def _fwd_ret(closes: np.ndarray, entry_bar: int, entry_price: float, n: int) -> float | None:
    k = entry_bar + n
    if k > len(closes) - 1 or entry_price <= 0:
        return None
    return round((float(closes[k]) - entry_price) / entry_price * 100.0, 4)


async def build_labels(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    stop_atr: float = 1.5,
    target_r: float = 2.0,
    time_stop: int = 20,
    trigger_window: int = 30,
) -> list[LabeledSetup]:
    """Label every candidate of a scan with its simulated trade outcome.

    Candidates are realigned to the freshly loaded bars BY DATE, not by the
    stored bar_index: get_bars_df returns "the last N bars as of now", so
    nightly syncs shift the window and stored indices drift for any symbol
    with more than N bars in the DB.

    The same per-symbol cooldown as Phase 2 applies (no overlapping trades),
    so the labeled stream matches what was actually tradable.
    """
    scan = (
        await session.execute(
            select(BacktestSignalScan).where(BacktestSignalScan.id == scan_id)
        )
    ).scalar_one_or_none()
    if scan is None:
        raise ValueError(f"scan {scan_id} not found")
    lookback_days = scan.lookback_days

    candidates = await load_candidates(session, scan_id)
    if not candidates:
        return []

    unique_symbols = sorted({c.symbol for c in candidates})
    await ensure_bars_loaded(session, unique_symbols, lookback_days)

    by_symbol: dict[str, list] = {}
    for c in candidates:
        by_symbol.setdefault(c.symbol, []).append(c)

    labeled: list[LabeledSetup] = []
    skipped_realign = 0

    for sym, sym_candidates in by_symbol.items():
        df = get_cached_bars(sym, lookback_days)
        if df is None or df.empty:
            continue

        opens = df["open"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        dates = df["date"].to_numpy()
        n_bars = len(df)

        sym_candidates.sort(key=lambda c: c.bar_index)
        next_eligible = 0

        for c in sym_candidates:
            # Realign by signal date. Fall back to the stored index only when
            # it still points at the right date; otherwise skip the row.
            target_date = pd.Timestamp(c.signal_date).to_datetime64()
            i = int(np.searchsorted(dates, target_date))
            if i >= n_bars or dates[i] != target_date:
                if c.bar_index < n_bars and dates[c.bar_index] == target_date:
                    i = c.bar_index
                else:
                    skipped_realign += 1
                    continue

            if i < next_eligible:
                continue

            sim = simulate_candidate(
                opens, highs, lows, closes, i,
                float(c.pivot_price), float(c.atr_at_signal),
                stop_atr=stop_atr, target_r=target_r,
                time_stop=time_stop, trigger_window=trigger_window,
                record_excursions=True,
            )

            if sim.triggered and sim.invalid:
                next_eligible = sim.trigger_bar + 1
                continue

            labeled.append(LabeledSetup(
                candidate_id=c.id,
                symbol=sym,
                signal_date=str(c.signal_date)[:10],
                features=c.features,
                triggered=sim.triggered,
                censored=sim.censored,
                days_to_trigger=sim.days_to_trigger,
                exit_reason=sim.exit_reason,
                r_multiple=round(sim.r_multiple, 4) if sim.r_multiple is not None else None,
                mae_r=sim.mae_r,
                mfe_r=sim.mfe_r,
                mae_pct=sim.mae_pct,
                mfe_pct=sim.mfe_pct,
                fwd_ret_5=_fwd_ret(closes, sim.trigger_bar, sim.entry_price, 5) if sim.triggered else None,
                fwd_ret_10=_fwd_ret(closes, sim.trigger_bar, sim.entry_price, 10) if sim.triggered else None,
                fwd_ret_20=_fwd_ret(closes, sim.trigger_bar, sim.entry_price, 20) if sim.triggered else None,
            ))

            next_eligible = (sim.exit_bar + 1) if sim.triggered else (i + trigger_window + 1)

    if skipped_realign:
        log.warning(
            "build_labels: %d candidates skipped — signal_date not found in current bars "
            "(bars window shifted since scan; consider a fresh scan)", skipped_realign,
        )
    log.info("build_labels: %d labeled setups from scan %s", len(labeled), scan_id)
    return labeled
