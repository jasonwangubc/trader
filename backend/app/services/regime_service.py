"""Market regime detection — Stage 2 / Stage 4 gate.

Distribution-day count (Minervini / IBD method):
  A distribution day is a session where SPY (or QQQ) closes DOWN ≥0.2% on
  HIGHER volume than the previous day. It signals institutional selling.
  Count over a rolling 25-trading-day window:
    0-2 → healthy
    3-4 → caution
    5+  → under pressure (regime → caution or bear depending on price)

Follow-through day (FTD):
  After a market correction, a follow-through day is a +1.25% gain on rising
  volume on day 4+ of a rally attempt. Confirms a new uptrend.

We use the simpler distribution-day count as the primary input because we don't
yet track intraday volume continuously (only EOD bars).


Minervini's rule: only buy breakouts when the broad market is in Stage 2
(above the 200-day SMA and trending up). Stop buying new positions when the
market is below the 200 SMA.

Regimes:
  bull    — price > 200 SMA, 200 SMA trending up. Green light.
  caution — price 0-5% below 200 SMA, or 200 SMA flat/declining. Proceed
            with extra selectivity.
  bear    — price > 5% below 200 SMA. Stop buying new positions.

We check both SPY (US) and XIU.TO (Canada) and return the worse of the two
so we're never buying Canadian breakouts in a US bear market.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DailyBar
from app.services.eod_service import get_bars_df

log = logging.getLogger(__name__)

BEAR_THRESHOLD = -0.05   # >5% below 200 SMA → bear
CAUTION_THRESHOLD = 0.0  # at or below 200 SMA → caution

BENCHMARKS = {"US": "SPY", "CA": "XIU.TO"}


@dataclass
class RegimeResult:
    regime: str            # "bull" | "caution" | "bear"
    spy_price: float | None = None
    spy_ma200: float | None = None
    spy_pct_vs_ma200: float | None = None
    xiu_price: float | None = None
    xiu_ma200: float | None = None
    xiu_pct_vs_ma200: float | None = None
    distribution_days: int = 0          # rolling 25-day count for SPY
    distribution_status: str = "healthy"  # "healthy" | "elevated" | "heavy"
    message: str = ""


def _classify(price: float, ma200: float) -> str:
    if ma200 <= 0:
        return "caution"
    pct = (price - ma200) / ma200
    if pct < BEAR_THRESHOLD:
        return "bear"
    if pct < CAUTION_THRESHOLD:
        return "caution"
    return "bull"


def _count_distribution_days(df) -> tuple[int, str]:
    """Count distribution days in the last 25 trading sessions.
    A distribution day: close down ≥0.2% AND volume > previous day's volume.
    """
    if df.empty or len(df) < 2:
        return 0, "healthy"

    window = min(26, len(df))  # 25 bars + 1 previous for comparison
    recent = df.tail(window)

    closes  = recent["close"].values.astype(float)
    volumes = recent["volume"].values.astype(int)

    count = 0
    for i in range(1, len(closes)):
        pct_change = (closes[i] - closes[i - 1]) / closes[i - 1]
        if pct_change <= -0.002 and volumes[i] > volumes[i - 1]:
            count += 1

    if count >= 5:
        status = "heavy"
    elif count >= 3:
        status = "elevated"
    else:
        status = "healthy"

    return count, status


_REGIME_RANK = {"bull": 0, "caution": 1, "bear": 2}


async def get_regime(session: AsyncSession) -> RegimeResult:
    """Compute current regime from stored daily bars, including distribution day count."""
    spy_df  = await get_bars_df(session, "SPY",    days=250)
    xiu_df  = await get_bars_df(session, "XIU.TO", days=250)

    result = RegimeResult(regime="caution", message="Insufficient data — treating as caution")

    regimes = []
    if len(spy_df) >= 200:
        closes = spy_df["close"].values.astype(float)
        ma200  = float(np.mean(closes[-200:]))
        price  = closes[-1]
        pct    = (price - ma200) / ma200
        r      = _classify(price, ma200)
        regimes.append(r)
        result.spy_price        = round(price, 2)
        result.spy_ma200        = round(ma200, 2)
        result.spy_pct_vs_ma200 = round(pct * 100, 2)

        # Distribution day count
        dist_count, dist_status = _count_distribution_days(spy_df)
        result.distribution_days   = dist_count
        result.distribution_status = dist_status

        # Distribution days degrade regime independently of price
        if dist_status == "heavy" and r == "bull":
            regimes.append("caution")  # push regime down even if price is above 200 SMA

    if len(xiu_df) >= 200:
        closes = xiu_df["close"].values.astype(float)
        ma200  = float(np.mean(closes[-200:]))
        price  = closes[-1]
        pct    = (price - ma200) / ma200
        r      = _classify(price, ma200)
        regimes.append(r)
        result.xiu_price        = round(price, 2)
        result.xiu_ma200        = round(ma200, 2)
        result.xiu_pct_vs_ma200 = round(pct * 100, 2)

    if regimes:
        result.regime = max(regimes, key=lambda r: _REGIME_RANK[r])
        dist_note = ""
        if result.distribution_days >= 5:
            dist_note = f" ({result.distribution_days} distribution days — heavy selling pressure.)"
        elif result.distribution_days >= 3:
            dist_note = f" ({result.distribution_days} distribution days — elevated.)"

        msgs = {
            "bull":    f"Above 200 SMA, market healthy.{dist_note} Full risk allocation.",
            "caution": f"Near/below 200 SMA or elevated distribution.{dist_note} Reduce size, favour A+ setups only.",
            "bear":    f"Market below 200 SMA.{dist_note} Stop buying new positions. Protect capital.",
        }
        result.message = msgs[result.regime]

    return result
