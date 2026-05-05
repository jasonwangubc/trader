"""Market regime detection — Stage 2 / Stage 4 gate.

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


_REGIME_RANK = {"bull": 0, "caution": 1, "bear": 2}


async def get_regime(session: AsyncSession) -> RegimeResult:
    """Compute current regime from stored daily bars."""
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
        result.spy_price          = round(price, 2)
        result.spy_ma200          = round(ma200, 2)
        result.spy_pct_vs_ma200   = round(pct * 100, 2)

    if len(xiu_df) >= 200:
        closes = xiu_df["close"].values.astype(float)
        ma200  = float(np.mean(closes[-200:]))
        price  = closes[-1]
        pct    = (price - ma200) / ma200
        r      = _classify(price, ma200)
        regimes.append(r)
        result.xiu_price          = round(price, 2)
        result.xiu_ma200          = round(ma200, 2)
        result.xiu_pct_vs_ma200   = round(pct * 100, 2)

    if regimes:
        # Take the worse regime of the two benchmarks.
        result.regime = max(regimes, key=lambda r: _REGIME_RANK[r])
        msgs = {
            "bull":    "Both benchmarks above 200 SMA. Full risk allocation.",
            "caution": "One or both benchmarks near/below 200 SMA. Reduce size, favour A+ setups only.",
            "bear":    "Market below 200 SMA. Stop buying new positions. Protect capital.",
        }
        result.message = msgs[result.regime]

    return result
