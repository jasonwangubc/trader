"""Phase-1 (signal scan) + Phase-2 (trade simulation) split for the backtest.

The expensive work — Trend Template score, VCP score, pattern detection, ATR —
depends only on (symbol, bar_index, lookback_days). It does NOT depend on the
trade-management parameters (stop multiplier, target multiple, time stop,
trigger window) or on filter thresholds (tt_min, pattern_quality_min).

So we split the engine in two:

  Phase 1: scan_universe — for every (symbol, bar), score patterns and persist
           every match into BacktestSignalCandidate. Slow (~25 min full universe),
           runs once. Stores ATR at signal so Phase-2 doesn't recompute it.

  Phase 2: simulate_from_scan — given a cached scan_id and trade params, load
           the candidate set, filter by thresholds, walk forward through bars
           to compute outcomes. Fast (~30 sec full universe).

Parameter sweeps reuse the same scan_id and just re-run Phase 2 with different
params. This makes sweeps cheap (N × 30s) instead of impractical (N × 25min).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    BacktestSignalCandidate,
    BacktestSignalScan,
    ScreenerSymbol,
)
from app.services.eod_service import get_bars_df
from app.services.ml_features import atr_scalar, extract_features
from app.services.pattern_service import detect_pattern
from app.services.trend_template import MIN_BARS, score_trend_template
from app.services.vcp_scorer import score_vcp

log = logging.getLogger(__name__)


# ─── In-process bars cache ────────────────────────────────────────────────────
# Keyed by (symbol, lookback_days). Loaded lazily by Phase 1, reused by Phase 2.
# Cleared when a new scan starts or process restarts.
_bars_cache: dict[tuple[str, int], pd.DataFrame] = {}


def _ma(closes: np.ndarray, n: int) -> float | None:
    if len(closes) < n:
        return None
    return float(np.mean(closes[-n:]))


# ─── Phase 1: scan universe ───────────────────────────────────────────────────


@dataclass
class CandidateRow:
    """Lightweight in-memory candidate built during scan, persisted in bulk."""
    symbol: str
    signal_date: datetime
    bar_index: int
    tt_score: int
    vcp_score: float
    pattern_type: str
    pattern_quality: float
    buyability: str
    pivot_price: float
    atr_at_signal: float
    features: dict | None = None


# Minimum signal-bar gap — same symbol can't fire on every consecutive bar even
# at scan time, to keep the candidate table from blowing up. Phase-2 still
# enforces its own per-symbol lockout based on trade outcome / trigger expiry.
SCAN_DEDUP_BARS = 5


async def scan_universe(
    session: AsyncSession,
    *,
    lookback_days: int = 504,
    symbols: list[str] | None = None,
    progress_callback=None,
) -> uuid.UUID:
    """Phase 1 — heavy. Scan every bar across the universe for pattern matches.

    Caches every (symbol, bar) where the detector returned a usable pattern
    (buyability in {at_pivot, in_base} with a pivot price). Threshold filters
    (tt_min, pattern_quality_min) are NOT applied here — Phase 2 filters
    post-hoc so sweeps over thresholds don't require re-scanning.

    Returns the scan_id. Bars stay in-process cache for Phase-2 reuse.
    """
    global _bars_cache
    _bars_cache.clear()    # fresh scan = fresh cache

    if symbols is None:
        sym_q = await session.execute(
            select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
        )
        symbols = [r for (r,) in sym_q.all()]

    spy_df = await get_bars_df(session, "SPY", days=lookback_days)
    if not spy_df.empty:
        _bars_cache[("SPY", lookback_days)] = spy_df

    # Create scan record
    scan_id = uuid.uuid4()
    scan = BacktestSignalScan(
        id=scan_id,
        lookback_days=lookback_days,
        symbols_scanned=len(symbols),
        candidate_count=0,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.add(scan)
    await session.commit()

    candidates: list[CandidateRow] = []
    completed = 0

    # SPY date array for point-in-time benchmark slicing (no look-ahead: the
    # benchmark window must end at the signal bar's date, not at "now").
    spy_dates = spy_df["date"].to_numpy() if not spy_df.empty else None

    try:
        for sym in symbols:
            df = await get_bars_df(session, sym, days=lookback_days)
            if df.empty or len(df) < MIN_BARS + 35:   # need room for trigger+sim later
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(symbols))
                continue
            _bars_cache[(sym, lookback_days)] = df

            closes = df["close"].values.astype(float)
            sym_dates = df["date"].to_numpy()
            # Per-bar SPY alignment: spy_cut[i] = number of SPY bars dated ≤ bar i.
            spy_cut = (
                np.searchsorted(spy_dates, sym_dates, side="right")
                if spy_dates is not None else None
            )
            last_signal_bar = -1
            scan_end = len(df) - 35   # leave room for trigger_window + time_stop in Phase 2

            for i in range(MIN_BARS, scan_end):
                if i - last_signal_bar < SCAN_DEDUP_BARS:
                    continue
                hist = df.iloc[:i + 1]
                spy_hist = spy_df.iloc[:int(spy_cut[i])] if spy_cut is not None else None

                tt = score_trend_template(hist, benchmark_df=spy_hist)
                if tt.score < 2:   # rock-bottom cutoff; sweepable threshold lives in Phase 2
                    continue

                ma_50  = _ma(closes[:i + 1], 50)
                ma_200 = _ma(closes[:i + 1], 200)
                pat = detect_pattern(hist, ma_50=ma_50, ma_200=ma_200)

                if pat.buyability not in ("at_pivot", "in_base"):
                    continue
                if pat.pivot_price is None or pat.pivot_price <= 0:
                    continue
                if pat.quality < 0.25:   # rock-bottom cutoff; sweepable in Phase 2
                    continue

                vcp = score_vcp(hist, tt)
                atr = atr_scalar(hist)

                try:
                    features = extract_features(
                        hist, spy_hist=spy_hist, tt=tt, vcp=vcp, pat=pat, atr=atr,
                    )
                except Exception:
                    log.exception("feature extraction failed for %s @ bar %d", sym, i)
                    features = None

                candidates.append(CandidateRow(
                    symbol=sym,
                    signal_date=df["date"].iloc[i],
                    bar_index=i,
                    tt_score=tt.score,
                    vcp_score=float(vcp.score),
                    pattern_type=pat.pattern_type,
                    pattern_quality=float(pat.quality),
                    buyability=pat.buyability,
                    pivot_price=float(pat.pivot_price),
                    atr_at_signal=atr,
                    features=features,
                ))
                last_signal_bar = i

            completed += 1
            if progress_callback:
                progress_callback(completed, len(symbols))

        # Bulk insert candidates
        if candidates:
            rows = [
                {
                    "scan_id": scan_id,
                    "symbol": c.symbol,
                    "signal_date": c.signal_date,
                    "bar_index": c.bar_index,
                    "tt_score": c.tt_score,
                    "vcp_score": c.vcp_score,
                    "pattern_type": c.pattern_type,
                    "pattern_quality": c.pattern_quality,
                    "buyability": c.buyability,
                    "pivot_price": c.pivot_price,
                    "atr_at_signal": c.atr_at_signal,
                    "features": c.features,
                }
                for c in candidates
            ]
            # Insert in chunks to avoid one huge statement
            CHUNK = 5000
            for j in range(0, len(rows), CHUNK):
                await session.execute(pg_insert(BacktestSignalCandidate.__table__), rows[j:j + CHUNK])
            await session.commit()

        # Mark scan complete
        scan.candidate_count = len(candidates)
        scan.status = "success"
        scan.finished_at = datetime.now(timezone.utc)
        await session.commit()

        log.info("scan %s: %d candidates across %d symbols", scan_id, len(candidates), len(symbols))
        return scan_id

    except Exception as exc:
        scan.status = "failed"
        scan.error = str(exc)[:480]
        scan.finished_at = datetime.now(timezone.utc)
        await session.commit()
        raise


async def latest_successful_scan(
    session: AsyncSession,
    *,
    lookback_days: int | None = None,
) -> BacktestSignalScan | None:
    """Newest successful scan. lookback_days=None matches any lookback —
    callers that just want "the freshest scan" (odds card, training) use that
    and read scan.lookback_days off the returned row."""
    conds = [BacktestSignalScan.status == "success"]
    if lookback_days is not None:
        conds.append(BacktestSignalScan.lookback_days == lookback_days)
    q = await session.execute(
        select(BacktestSignalScan)
        .where(*conds)
        .order_by(BacktestSignalScan.finished_at.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()


async def load_candidates(
    session: AsyncSession,
    scan_id: uuid.UUID,
) -> list[BacktestSignalCandidate]:
    q = await session.execute(
        select(BacktestSignalCandidate)
        .where(BacktestSignalCandidate.scan_id == scan_id)
        .order_by(BacktestSignalCandidate.symbol, BacktestSignalCandidate.bar_index)
    )
    return list(q.scalars().all())


async def ensure_bars_loaded(
    session: AsyncSession,
    symbols: list[str],
    lookback_days: int,
) -> None:
    """Hydrate the in-process bars cache for any symbols not already loaded.

    Phase 2 needs bars to walk forward through trigger/exit windows. Normally
    Phase 1 already populated the cache; this is for cold-start (Phase 2 run
    against a scan from a different process restart).
    """
    missing = [s for s in symbols if (s, lookback_days) not in _bars_cache]
    if not missing:
        return
    spy_key = ("SPY", lookback_days)
    if spy_key not in _bars_cache:
        spy_df = await get_bars_df(session, "SPY", days=lookback_days)
        if not spy_df.empty:
            _bars_cache[spy_key] = spy_df
    for sym in missing:
        df = await get_bars_df(session, sym, days=lookback_days)
        if not df.empty:
            _bars_cache[(sym, lookback_days)] = df


def get_cached_bars(symbol: str, lookback_days: int) -> pd.DataFrame | None:
    return _bars_cache.get((symbol, lookback_days))
