"""Lenient VCP (Volatility Contraction Pattern) scorer — returns a 0.0–1.0
likelihood score, NOT a boolean pass/fail.

Philosophy: Real VCPs are visually identifiable but almost never match strict
mathematical contraction ratios. Err toward leniency — surface candidates for
human visual confirmation rather than filtering them out with strict rules.

Scoring components (total 10 points → normalized to 0.0–1.0):
  1. Base tightness now (2 pts)   — tight recent range = contraction in progress
  2. Volatility compression (2 pts) — ATR declining over the base
  3. Volume contraction (2 pts)   — average volume falling during the base
  4. Near pivot (2 pts)           — price in upper third of the base
  5. Trend alignment (2 pts)      — stock is in a valid Stage 2 uptrend (TT ≥ 5)

The score is intentionally forgiving: a stock with 4/10 shows up at 0.4 and is
still worth a glance. The user applies their own visual filter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.services.trend_template import TTResult

log = logging.getLogger(__name__)

BASE_WINDOW = 30    # days to analyse for the current base
PRIOR_WINDOW = 60   # look-back for prior contraction comparison


@dataclass
class VCPResult:
    score: float                            # 0.0 – 1.0
    details: dict[str, float] = field(default_factory=dict)
    base_depth_pct: float | None = None    # high-to-low % in current base
    atr_ratio: float | None = None         # current ATR / prior ATR (< 1 = contracting)
    volume_ratio: float | None = None      # recent avg vol / prior avg vol


# Minimum average daily range as % of price. Below this the stock is frozen —
# most likely in acquisition limbo or halted. Not a tradeable setup.
MIN_ADR_PCT = 0.50   # 50 basis points — real stocks always exceed this

# Minimum base depth for a valid VCP. Acquisition targets lock to the offer
# price and show 1-2% "bases" that are purely price pinning, not real bases.
MIN_BASE_DEPTH_PCT = 0.025   # 2.5%


def score_vcp(df: pd.DataFrame, tt: TTResult) -> VCPResult:
    """
    df: daily bars sorted ascending, columns [date, close, high, low, volume].
    tt: TTResult from score_trend_template for the same symbol.
    """
    if df.empty or len(df) < PRIOR_WINDOW + BASE_WINDOW:
        return VCPResult(score=0.0)

    closes = df["close"].values.astype(float)
    highs  = df["high"].values.astype(float)
    lows   = df["low"].values.astype(float)
    vols   = df["volume"].values.astype(float)

    # ── 0. Frozen-stock guard ────────────────────────────────────────────────
    # Acquisition targets / halted stocks have essentially zero daily movement.
    # Their "tight base" is price-pinning to an offer price, not organic contraction.
    # Reject before scoring — these should not rank as VCP candidates.
    recent_closes = closes[-BASE_WINDOW:]
    recent_highs  = highs[-BASE_WINDOW:]
    recent_lows   = lows[-BASE_WINDOW:]
    avg_daily_range = float(np.mean((recent_highs - recent_lows) / recent_closes)) * 100
    if avg_daily_range < MIN_ADR_PCT:
        return VCPResult(score=0.0, details={"frozen": True, "avg_adr_pct": round(avg_daily_range, 3)})

    # ── 1. Base tightness ────────────────────────────────────────────────────
    base_h = float(np.max(highs[-BASE_WINDOW:]))
    base_l = float(np.min(lows[-BASE_WINDOW:]))
    base_depth = (base_h - base_l) / base_h if base_h > 0 else 1.0

    # Reject impossibly tight bases (< 2.5%) — acquisition pinning, not real contraction
    if base_depth < MIN_BASE_DEPTH_PCT:
        return VCPResult(score=0.0, base_depth_pct=base_depth * 100,
                         details={"frozen": True, "base_depth_pct": round(base_depth * 100, 2)})

    # Tight = depth < 10% → 2 pts; decent < 15% → 1 pt; loose → 0
    if base_depth < 0.10:
        tightness_score = 2.0
    elif base_depth < 0.15:
        tightness_score = 1.0
    else:
        tightness_score = max(0.0, 2.0 * (1.0 - base_depth / 0.25))

    # ── 2. Volatility compression (ATR ratio) ────────────────────────────────
    def atr(h, l, c_prev):
        tr = np.maximum(h[1:] - l[1:],
               np.maximum(np.abs(h[1:] - c_prev[:-1]),
                          np.abs(l[1:] - c_prev[:-1])))
        return float(np.mean(tr))

    n = BASE_WINDOW
    p = PRIOR_WINDOW
    curr_atr = atr(highs[-n:], lows[-n:], closes[-n-1:-1])
    prior_atr = atr(highs[-p:-n], lows[-p:-n], closes[-p-1:-n-1]) if len(closes) >= p + n + 1 else curr_atr

    atr_ratio = curr_atr / prior_atr if prior_atr > 0 else 1.0
    # Contracting = ratio < 1; deep compression < 0.7 → 2 pts
    if atr_ratio < 0.70:
        compression_score = 2.0
    elif atr_ratio < 0.90:
        compression_score = 1.0
    else:
        compression_score = max(0.0, 2.0 * (1.0 - atr_ratio))

    # ── 3. Volume contraction ────────────────────────────────────────────────
    recent_vol  = float(np.mean(vols[-n:]))
    prior_vol   = float(np.mean(vols[-p:-n])) if len(vols) >= p + n else recent_vol
    vol_ratio   = recent_vol / prior_vol if prior_vol > 0 else 1.0

    if vol_ratio < 0.70:
        volume_score = 2.0
    elif vol_ratio < 0.90:
        volume_score = 1.0
    else:
        volume_score = max(0.0, 2.0 * (1.0 - vol_ratio))

    # ── 4. Near pivot (price in upper third of base) ─────────────────────────
    price = closes[-1]
    base_range = base_h - base_l
    if base_range > 0:
        position_in_base = (price - base_l) / base_range   # 0=bottom, 1=top
    else:
        position_in_base = 0.5

    if position_in_base >= 0.80:
        pivot_score = 2.0
    elif position_in_base >= 0.60:
        pivot_score = 1.0
    else:
        pivot_score = max(0.0, position_in_base * 2.0)

    # ── 5. Trend alignment ───────────────────────────────────────────────────
    # Full 2 pts for TT ≥ 6, partial for 4-5, zero for ≤ 3
    if tt.score >= 6:
        trend_score = 2.0
    elif tt.score >= 4:
        trend_score = 1.0
    elif tt.score >= 2:
        trend_score = 0.5
    else:
        trend_score = 0.0

    total = tightness_score + compression_score + volume_score + pivot_score + trend_score
    normalized = round(min(total / 10.0, 1.0), 3)

    return VCPResult(
        score=normalized,
        details={
            "tightness":    round(tightness_score, 2),
            "compression":  round(compression_score, 2),
            "volume":       round(volume_score, 2),
            "pivot":        round(pivot_score, 2),
            "trend":        round(trend_score, 2),
        },
        base_depth_pct=round(base_depth * 100, 2),
        atr_ratio=round(atr_ratio, 3),
        volume_ratio=round(vol_ratio, 3),
    )
