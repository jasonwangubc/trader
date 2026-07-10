"""ML feature extractor: train/serve parity contract + shape guarantees.

The critical test here is parity: the features persisted by scan_universe
(Phase-1, training data) must be byte-identical to what an independent call
to extract_features produces on the same slices — because run_screener uses
that same call at serving time.
"""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.db.models import BacktestSignalCandidate, DailyBar
from app.services import signal_scan_service
from app.services.eod_service import get_bars_df
from app.services.ml_features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    atr_scalar,
    extract_features,
)
from app.services.pattern_service import detect_pattern
from app.services.signal_scan_service import scan_universe
from app.services.trend_template import score_trend_template
from app.services.vcp_scorer import score_vcp


def _uptrend_with_base(n_trend: int = 250, n_base: int = 50) -> tuple[list, list, list]:
    """Deterministic uptrend 50→98 followed by a flat-ish base under 100."""
    highs, lows, closes = [], [], []
    for i in range(n_trend):
        c = 50.0 + (98.0 - 50.0) * i / (n_trend - 1)
        closes.append(round(c, 2))
        highs.append(round(c * 1.012, 2))
        lows.append(round(c * 0.988, 2))
    base_cycle = [96.0, 97.0, 98.5, 97.5, 96.5, 97.0, 98.0, 97.2, 96.8, 97.5]
    for j in range(n_base):
        c = base_cycle[j % len(base_cycle)]
        closes.append(c)
        highs.append(min(round(c * 1.01, 2), 100.0))
        lows.append(round(c * 0.99, 2))
    return highs, lows, closes


def _bars_df(highs: list, lows: list, closes: list) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=len(closes))
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1_000_000] * len(closes),
    })


def _seed_bars(session, symbol: str, df: pd.DataFrame) -> None:
    session.add_all([
        DailyBar(
            symbol=symbol,
            bar_date=row["date"].to_pydatetime(),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=int(row["volume"]),
            adj_close=Decimal(str(row["close"])),
        )
        for _, row in df.iterrows()
    ])


def _spy_df(n: int) -> pd.DataFrame:
    closes = [round(400.0 + 100.0 * i / (n - 1), 2) for i in range(n)]
    return _bars_df(
        [round(c * 1.005, 2) for c in closes],
        [round(c * 0.995, 2) for c in closes],
        closes,
    )


def _scorer_inputs(df: pd.DataFrame, spy_df: pd.DataFrame, i: int):
    """Replicate exactly what scan_universe computes at bar i."""
    hist = df.iloc[: i + 1]
    spy_dates = spy_df["date"].to_numpy()
    k = int(np.searchsorted(spy_dates, df["date"].to_numpy()[i], side="right"))
    spy_hist = spy_df.iloc[:k]
    closes = df["close"].values.astype(float)
    tt = score_trend_template(hist, benchmark_df=spy_hist)
    ma_50 = float(np.mean(closes[: i + 1][-50:])) if i + 1 >= 50 else None
    ma_200 = float(np.mean(closes[: i + 1][-200:])) if i + 1 >= 200 else None
    pat = detect_pattern(hist, ma_50=ma_50, ma_200=ma_200)
    vcp = score_vcp(hist, tt)
    return hist, spy_hist, tt, vcp, pat


async def test_scan_features_match_independent_extraction(db_session):
    """Parity contract: stored candidate features == recomputed features."""
    signal_scan_service._bars_cache.clear()
    highs, lows, closes = _uptrend_with_base()
    df = _bars_df(highs, lows, closes)
    _seed_bars(db_session, "PARITY", df)
    _seed_bars(db_session, "SPY", _spy_df(len(df)))
    await db_session.commit()

    scan_id = await scan_universe(db_session, lookback_days=504, symbols=["PARITY"])

    q = await db_session.execute(
        select(BacktestSignalCandidate).where(BacktestSignalCandidate.scan_id == scan_id)
    )
    candidates = list(q.scalars().all())
    assert candidates, "synthetic series produced no candidates — parity test is vacuous"

    df_db = await get_bars_df(db_session, "PARITY", days=504)
    spy_db = await get_bars_df(db_session, "SPY", days=504)

    for cand in candidates:
        assert cand.features is not None
        assert cand.features["fv"] == FEATURE_VERSION
        hist, spy_hist, tt, vcp, pat = _scorer_inputs(df_db, spy_db, cand.bar_index)
        recomputed = extract_features(
            hist, spy_hist=spy_hist, tt=tt, vcp=vcp, pat=pat, atr=atr_scalar(hist),
        )
        assert recomputed == cand.features


def test_feature_names_are_exactly_the_extractor_output():
    highs, lows, closes = _uptrend_with_base()
    df = _bars_df(highs, lows, closes)
    spy = _spy_df(len(df))
    hist, spy_hist, tt, vcp, pat = _scorer_inputs(df, spy, len(df) - 1)
    out = extract_features(hist, spy_hist=spy_hist, tt=tt, vcp=vcp, pat=pat, atr=atr_scalar(hist))

    assert set(out.keys()) == {"fv"} | set(FEATURE_NAMES)
    for name, val in out.items():
        assert val is None or isinstance(val, (int, float)), f"{name} has type {type(val)}"


def test_features_are_point_in_time():
    """Appending future bars to the source frame must not change features
    extracted from a historical slice."""
    highs, lows, closes = _uptrend_with_base()
    df_short = _bars_df(highs, lows, closes)
    # 30 more bars of a huge rally that would corrupt look-ahead-sensitive stats
    highs2 = highs + [round(150 + i, 2) for i in range(30)]
    lows2 = lows + [round(145 + i, 2) for i in range(30)]
    closes2 = closes + [round(148 + i, 2) for i in range(30)]
    df_long = _bars_df(highs2, lows2, closes2)
    spy = _spy_df(len(df_long))

    i = len(df_short) - 1
    for frame in (df_short, df_long):
        assert frame["date"].iloc[i] == df_short["date"].iloc[i]

    h1, s1, tt1, vcp1, pat1 = _scorer_inputs(df_short, spy, i)
    h2, s2, tt2, vcp2, pat2 = _scorer_inputs(df_long, spy, i)
    f1 = extract_features(h1, spy_hist=s1, tt=tt1, vcp=vcp1, pat=pat1, atr=atr_scalar(h1))
    f2 = extract_features(h2, spy_hist=s2, tt=tt2, vcp=vcp2, pat=pat2, atr=atr_scalar(h2))
    assert f1 == f2
