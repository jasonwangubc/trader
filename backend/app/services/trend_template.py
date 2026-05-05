"""Minervini Trend Template — 8 criteria, scored 0-8.

Scoring philosophy (per user preference): score each criterion independently.
A stock doesn't need all 8 to be worth watching; 6+ is stage-2 quality.

Criteria:
  1. Price > 150 SMA AND price > 200 SMA
  2. 150 SMA > 200 SMA
  3. 200 SMA is trending up (today vs 21 trading days ago)
  4. 50 SMA > 150 SMA AND 50 SMA > 200 SMA
  5. Price > 50 SMA
  6. Price is at least 25% above its 52-week low
  7. Price is within 25% of its 52-week high
  8. Relative strength vs benchmark (rs_raw > 1.0 = outperforming)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MIN_BARS = 210  # need 200 SMA + a few extra


@dataclass
class TTResult:
    score: int                          # 0-8
    criteria: dict[str, bool] = field(default_factory=dict)
    ma_50: float | None = None
    ma_150: float | None = None
    ma_200: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    last_close: float | None = None
    rs_raw: float | None = None         # stock_return / benchmark_return (1Y)


def score_trend_template(
    df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None = None,
) -> TTResult:
    """
    df: daily bars DataFrame with columns [date, close, volume].
        Must be sorted ascending and have at least MIN_BARS rows.
    benchmark_df: same format for SPY (or XIU.TO). Used for criterion 8.
    """
    if df.empty or len(df) < MIN_BARS:
        return TTResult(score=0)

    closes = df["close"].values.astype(float)
    price = closes[-1]

    ma_50 = float(np.mean(closes[-50:]))
    ma_150 = float(np.mean(closes[-150:]))
    ma_200 = float(np.mean(closes[-200:]))
    ma_200_21d_ago = float(np.mean(closes[-221:-21])) if len(closes) >= 221 else ma_200

    high_52w = float(np.max(closes[-252:])) if len(closes) >= 252 else float(np.max(closes))
    low_52w = float(np.min(closes[-252:])) if len(closes) >= 252 else float(np.min(closes))

    # RS vs benchmark (1-year return ratio)
    rs_raw: float | None = None
    if benchmark_df is not None and len(benchmark_df) >= 252:
        bench_closes = benchmark_df["close"].values.astype(float)
        stock_1y = closes[-1] / closes[-252] if len(closes) >= 252 else None
        bench_1y = bench_closes[-1] / bench_closes[-252]
        if stock_1y is not None and bench_1y > 0:
            rs_raw = stock_1y / bench_1y

    criteria: dict[str, bool] = {
        "price_above_150_200":   price > ma_150 and price > ma_200,
        "ma_150_above_200":      ma_150 > ma_200,
        "ma_200_trending_up":    ma_200 > ma_200_21d_ago,
        "ma_50_above_150_200":   ma_50 > ma_150 and ma_50 > ma_200,
        "price_above_50":        price > ma_50,
        "pct_above_52w_low":     price >= low_52w * 1.25,
        "within_25pct_52w_high": price >= high_52w * 0.75,
        "rs_outperforming":      rs_raw is not None and rs_raw > 1.0,
    }
    score = sum(1 for v in criteria.values() if v)

    return TTResult(
        score=score,
        criteria=criteria,
        ma_50=round(ma_50, 4),
        ma_150=round(ma_150, 4),
        ma_200=round(ma_200, 4),
        high_52w=round(high_52w, 4),
        low_52w=round(low_52w, 4),
        last_close=round(price, 4),
        rs_raw=round(rs_raw, 4) if rs_raw is not None else None,
    )
