"""Pivot unification regression tests.

The screener table and the chart page must produce the same pivot for the
same bars: the high of the most recent contraction (chart-page definition),
never the mean of the base's swing highs, never with a hidden x1.005 buffer.
"""
from __future__ import annotations

import pandas as pd

from app.services.pattern_service import _detect_base, compute_buy_pivot, detect_pattern


def _df(highs: list[float], lows: list[float], closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    dates = pd.bdate_range("2025-01-02", periods=n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


def _flat_base_df() -> pd.DataFrame:
    """100-bar uptrend 50->98, then a 40-bar base topped at 100 that finishes
    with a tight 10-bar contraction whose high is exactly 98.00."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []

    # Uptrend: 100 bars, ~2% daily range.
    for i in range(100):
        c = 50.0 + (98.0 - 50.0) * i / 99.0
        closes.append(round(c, 2))
        highs.append(round(c * 1.01, 2))
        lows.append(round(c * 0.99, 2))

    # Base bars 0-9: swing high at 100 on bar 3, neighbors ~95-96.
    base_closes_1 = [94.0, 95.0, 96.0, 98.0, 96.0, 95.0, 94.0, 93.5, 93.0, 92.5]
    for j, c in enumerate(base_closes_1):
        closes.append(c)
        highs.append(100.0 if j == 3 else round(c * 1.012, 2))
        lows.append(round(c * 0.985, 2))

    # Base bars 10-29: choppy middle, dips to ~88 (keeps 3WT out of range).
    base_closes_2 = [91.0, 90.0, 89.0, 88.5, 88.0, 89.0, 90.0, 91.0, 92.0, 93.0,
                     92.0, 91.0, 90.0, 89.5, 90.5, 91.5, 92.5, 93.5, 94.0, 95.0]
    for c in base_closes_2:
        closes.append(c)
        highs.append(round(c * 1.015, 2))
        lows.append(round(c * 0.985, 2))

    # Base bars 30-39: tight finish (~1.6% daily range), high capped at 98.00.
    base_closes_3 = [96.5, 96.8, 97.0, 97.2, 97.0, 96.8, 97.1, 97.3, 97.2, 97.4]
    for c in base_closes_3:
        closes.append(c)
        highs.append(min(round(c * 1.008, 2), 98.0))
        lows.append(round(c * 0.992, 2))
    # Make the tight-window max exactly 98.00 on the last bar.
    highs[-1] = 98.0

    return _df(highs, lows, closes)


def test_compute_buy_pivot_tight_window_high_no_buffer():
    df = _flat_base_df()
    pivot = compute_buy_pivot(df)
    # Exactly the tight-window high — 2dp, no x1.005 buffer (would be 98.49).
    assert pivot == 98.0


def test_detect_base_pivot_is_recent_contraction_not_zone_mean():
    df = _flat_base_df()
    base = _detect_base(df)
    assert base is not None
    # Structure: rim at 100, but the buy point is the recent contraction high.
    assert base["base_high"] == 100.0
    assert base["pivot_price"] == 98.0
    # Depth measured from the true rim.
    assert base["base_depth_pct"] > 10.0


def test_detect_pattern_pivot_matches_chart_definition():
    """The screener persists detect_pattern's pivot; the chart page now calls
    the same function — both must equal the hand-known contraction high."""
    df = _flat_base_df()
    result = detect_pattern(df)
    assert result.pivot_price == 98.0
    # And it's identical to what the chart path computes.
    assert result.pivot_price == compute_buy_pivot(df, base_high=result.base_high)


def test_mid_base_falls_back_to_rim():
    """Price deep in the base: a local high 20% under the rim is not a pivot —
    the rim remains the actionable buy point."""
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    # Uptrend to 100.
    for i in range(80):
        c = 60.0 + (99.0 - 60.0) * i / 79.0
        closes.append(round(c, 2))
        highs.append(round(c * 1.01, 2))
        lows.append(round(c * 0.99, 2))
    # Peak bar at 100.
    closes.append(99.0)
    highs.append(100.0)
    lows.append(97.5)
    # Decline to ~72, then chop 70-80 with wide ranges (not tight).
    for i in range(30):
        c = 99.0 - (99.0 - 72.0) * i / 29.0
        closes.append(round(c, 2))
        highs.append(round(c * 1.02, 2))
        lows.append(round(c * 0.975, 2))
    for i in range(30):
        c = 72.0 + 6.0 * ((i % 6) / 5.0)
        closes.append(round(c, 2))
        highs.append(round(c * 1.025, 2))
        lows.append(round(c * 0.97, 2))

    df = _df(highs, lows, closes)
    pivot = compute_buy_pivot(df, base_high=100.0)
    assert pivot == 100.0  # recent local high (~80) < 90% of rim -> rim wins


def test_no_pivot_on_tiny_dataframe():
    df = _flat_base_df().head(10)
    assert compute_buy_pivot(df) is None


def test_detect_pattern_surfaces_scorer_internals():
    """The ML feature extractor reads pattern internals from details — the
    refactor that exposed them must keep populating these keys."""
    result = detect_pattern(_flat_base_df())
    assert result.details["zone_count"] >= 1
    vm = result.details["vcp_metrics"]
    assert {"n_contractions", "last_contraction_pct", "progression",
            "tightness_score", "vol_score"} <= set(vm.keys())
    assert vm["n_contractions"] >= 1
    cm = result.details["cwh_metrics"]
    assert {"handle_depth_pct", "handle_bars", "handle_position",
            "handle_vol_ratio", "rim_ratio", "symmetry_score", "pip_score"} <= set(cm.keys())
