"""Chart pattern detection — identify Minervini/O'Neil-style setups and rate buyability.

The core insight this module enforces: a stock that's already up 200% is NOT
buyable, regardless of how strong its trend or fundamentals look. Buyability
comes from the pivot of a properly-formed base. Extension past the pivot is
the single most important filter that separates "leader to watch" from
"setup to act on."

Outputs per stock:
  - buyability      : at_pivot | in_base | extended | broken | no_pattern
  - pattern_type    : vcp | cwh | flat_base | high_tight_flag | none
  - pattern_quality : 0.0-1.0 score for the matched pattern
  - pivot_price     : the buy point (top of base)
  - base depth/length, extension % from pivot

Detection is geometric and rule-based. It approximates what a trained eye sees;
nothing here is statistical learning. False positives are acceptable — the user
visually confirms before committing capital.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


Buyability = Literal["at_pivot", "in_base", "extended", "broken", "frozen", "no_pattern"]
PatternType = Literal["vcp", "cwh", "flat_base", "high_tight_flag", "ascending_triangle", "three_weeks_tight", "bull_flag", "none"]


@dataclass
class PatternResult:
    pattern_type: PatternType = "none"
    quality: float = 0.0                    # 0-1, pattern-specific quality

    pivot_price: float | None = None        # the buy point — top of base
    base_low: float | None = None
    base_high: float | None = None
    base_length_days: int | None = None     # length of consolidation in trading days
    base_depth_pct: float | None = None     # (pivot - base_low) / pivot * 100

    extension_pct: float | None = None      # (current - pivot) / pivot * 100
    buyability: Buyability = "no_pattern"

    details: dict = field(default_factory=dict)


# ─── Tunable parameters ───────────────────────────────────────────────────────

_SWING_WINDOW = 5                # bars on either side for swing-high/low (fallback)
_SMOOTH_PERIOD = 5               # EMA span for noise suppression before pivot detection
_ATR_PERIOD = 14                 # ATR lookback for volatility-scaled zigzag
_ATR_ZIGZAG_MULT = 1.5           # confirm a reversal only when it exceeds k·ATR
_MIN_BASE_DAYS = 25              # 5 weeks — minimum to call something a "base"
_MAX_BASE_DEPTH_PCT = 35.0       # >35% deep = "wide and loose," not buyable
_MIN_BASE_DEPTH_PCT = 2.5        # <2.5% = acquisition pinning, not a real base
_AT_PIVOT_RANGE_PCT = (-3.0, 5.0)  # within -3% to +5% of pivot = at_pivot
_EXTENDED_THRESHOLD_PCT = 5.0    # > 5% past pivot = extended (Minervini's rule)
_BROKEN_BELOW_50MA_PCT = -3.0    # > 3% below 50ma = broken
_FROZEN_ADR_PCT = 0.50           # avg daily range < 0.5% of price = frozen/acquired
_PIVOT_ZONE_TOLERANCE_PCT = 3.0  # swing highs within ±3% form a resistance zone
_BASE_NEAR_52W_PCT = 0.75        # pivot must be ≥ 75% of 52w high (was 0.88 — widened)
_QUALITY_FLOOR = 0.25            # below this, fall back to "none"


# ─── Smoothing + soft-penalty helpers ─────────────────────────────────────────
# These are the noise-tolerance layer that the literature converges on:
#   • Lo-Mamaysky-Wang (2000) smooth with kernel regression before extrema detection.
#   • ATR-scaled zigzag (TrendSpider, AmiBroker, ChartSchool) replaces fixed-bar
#     swing windows with volatility-adjusted confirmation.
#   • Soft penalties (gaussian / sigmoid) replace hard `return 0.0` cliffs so a
#     pattern with one borderline metric is degraded, not deleted.

def _smooth(series: np.ndarray, period: int = _SMOOTH_PERIOD) -> np.ndarray:
    """EMA smoothing — cheap proxy for Lo-Mamaysky kernel regression.

    We use this to find pivot *locations* on a denoised curve, then read the
    *raw* OHLC value at that location for downstream geometry. That way the
    smoother only filters noise; it doesn't bias measured depths.
    """
    if len(series) < period:
        return series
    return pd.Series(series).ewm(span=period, adjust=False).mean().to_numpy()


def _atr(df: pd.DataFrame, period: int = _ATR_PERIOD) -> np.ndarray:
    """Wilder's ATR. Returns an array same length as df (early values may be NaN)."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    if len(close) < 2:
        return np.zeros_like(close)
    prev_c = np.concatenate(([close[0]], close[:-1]))
    tr = np.maximum.reduce([high - low, np.abs(high - prev_c), np.abs(low - prev_c)])
    return pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()


def _soft_window(x: float, lo: float, hi: float, *,
                 falloff: float = 0.5) -> float:
    """Trapezoidal soft window. Returns 1.0 inside [lo, hi], decays smoothly
    outside. `falloff` is the fraction of (hi-lo) over which the score drops
    to ~0 outside the window.

    This replaces hard `if x < lo or x > hi: return 0.0` cliffs. A value just
    outside the ideal range scores ~0.7-0.9 instead of being deleted.
    """
    width = hi - lo
    if width <= 0:
        return 0.0
    if lo <= x <= hi:
        return 1.0
    decay = max(width * falloff, 1e-9)
    if x < lo:
        d = (lo - x) / decay
    else:
        d = (x - hi) / decay
    return float(np.exp(-d * d))   # gaussian tail beyond the window


def _gaussian(x: float, center: float, width: float) -> float:
    """Gaussian centered at `center`, with characteristic width. Score peaks at 1.0."""
    if width <= 0:
        return 0.0
    d = (x - center) / width
    return float(np.exp(-d * d))


# ─── ATR-scaled zigzag ────────────────────────────────────────────────────────

def _atr_zigzag(df: pd.DataFrame, atr_mult: float = _ATR_ZIGZAG_MULT) -> list[tuple[int, str]]:
    """Identify swing pivots by ATR-confirmed reversal.

    Walks bars: maintains the latest extreme in the current direction and flips
    direction only when price reverses by more than k·ATR from that extreme.
    Returns a list of (index, 'H'|'L') in chronological order.

    This is volatility-adaptive — high-ATR names need larger reversals to count
    as swings, low-ATR names register tighter swings. Fixed-bar windows miss this.
    """
    n = len(df)
    if n < _ATR_PERIOD + 2:
        return []
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    atr = _atr(df)
    pivots: list[tuple[int, str]] = []

    direction = 0           # +1 = looking for higher highs; -1 = lower lows
    ext_idx = 0
    ext_price = high[0]
    for i in range(1, n):
        thr = atr[i] * atr_mult if np.isfinite(atr[i]) else 0.0
        if thr <= 0:
            continue
        if direction >= 0:
            # tracking an up-swing — update if higher, flip on enough drop
            if high[i] >= ext_price:
                ext_price = high[i]
                ext_idx = i
            elif ext_price - low[i] >= thr:
                pivots.append((ext_idx, "H"))
                direction = -1
                ext_price = low[i]
                ext_idx = i
        if direction <= 0:
            # tracking a down-swing — update if lower, flip on enough rise
            if low[i] <= ext_price:
                ext_price = low[i]
                ext_idx = i
            elif high[i] - ext_price >= thr:
                pivots.append((ext_idx, "L"))
                direction = 1
                ext_price = high[i]
                ext_idx = i

    # Always close with the running extreme so the most-recent leg is visible
    if pivots:
        last_dir = pivots[-1][1]
        if last_dir == "H":
            pivots.append((ext_idx, "L"))
        else:
            pivots.append((ext_idx, "H"))
    return pivots


# ─── Smoothed swing detection (backward-compatible API) ───────────────────────

def _swing_highs(df: pd.DataFrame, window: int = _SWING_WINDOW) -> list[int]:
    """Swing highs detected on a SMOOTHED high series, then mapped back to the
    raw bar's index within the window. Removes most gap/spike false swings.
    """
    n = len(df)
    if n < 2 * window + 1:
        return []
    raw_high = df["high"].to_numpy()
    sm = _smooth(raw_high)
    out: list[int] = []
    for i in range(window, n - window):
        if sm[i] == sm[i - window : i + window + 1].max():
            # Snap to the raw high inside the smoothing window — that's the
            # true pivot bar (smoothing may shift the peak by 1-2 bars).
            lo_i = max(0, i - window)
            hi_i = min(n, i + window + 1)
            raw_peak = lo_i + int(np.argmax(raw_high[lo_i:hi_i]))
            if not out or out[-1] != raw_peak:
                out.append(raw_peak)
    return out


def _swing_lows(df: pd.DataFrame, window: int = _SWING_WINDOW) -> list[int]:
    n = len(df)
    if n < 2 * window + 1:
        return []
    raw_low = df["low"].to_numpy()
    sm = _smooth(raw_low)
    out: list[int] = []
    for i in range(window, n - window):
        if sm[i] == sm[i - window : i + window + 1].min():
            lo_i = max(0, i - window)
            hi_i = min(n, i + window + 1)
            raw_trough = lo_i + int(np.argmin(raw_low[lo_i:hi_i]))
            if not out or out[-1] != raw_trough:
                out.append(raw_trough)
    return out


# ─── Perceptually Important Points (Chung et al.) ─────────────────────────────
# PIPs are the n most "visually salient" points on a curve, selected greedily by
# vertical distance from the line connecting already-chosen neighbours. They are
# how academic CWH / H&S detectors describe pattern shape independent of length.

def _pip_extract(prices: np.ndarray, n_points: int) -> list[int]:
    """Extract n perceptually important point INDICES from a price series.

    Greedy: start with endpoints, repeatedly add the point with the largest
    vertical distance from the line segment between its bracketing PIPs.
    Returns sorted indices.
    """
    n = len(prices)
    if n < 2 or n_points < 2:
        return list(range(min(n, max(0, n_points))))
    if n_points >= n:
        return list(range(n))

    pips = [0, n - 1]
    while len(pips) < n_points:
        best_dist = -1.0
        best_idx = -1
        # find segment with point of max vertical distance
        for a, b in zip(pips[:-1], pips[1:], strict=True):
            if b - a < 2:
                continue
            x = np.arange(a, b + 1, dtype=float)
            # line between (a, prices[a]) and (b, prices[b])
            if b == a:
                continue
            line = prices[a] + (prices[b] - prices[a]) * (x - a) / (b - a)
            diffs = np.abs(prices[a:b + 1] - line)
            local_best = int(np.argmax(diffs))
            if diffs[local_best] > best_dist:
                best_dist = float(diffs[local_best])
                best_idx = a + local_best
        if best_idx <= 0 or best_idx in pips:
            break
        pips.append(best_idx)
        pips.sort()
    return pips


def _pip_normalize(prices: np.ndarray, idxs: list[int]) -> np.ndarray:
    """Normalize PIP values to [0, 1] over the local price range so two patterns
    of different absolute price can be shape-compared."""
    if not idxs:
        return np.array([])
    vals = prices[np.asarray(idxs)]
    p_min, p_max = float(np.min(vals)), float(np.max(vals))
    rng = p_max - p_min
    if rng <= 0:
        return np.full_like(vals, 0.5, dtype=float)
    return (vals - p_min) / rng


# Template PIPs for a textbook cup-with-handle, 7 points: left-rim, left-slope-down,
# cup-bottom, right-slope-up, right-rim, handle-low, current. Values are normalized
# to the [0, 1] price range over the base.
_CWH_TEMPLATE_PIPS = np.array([
    1.00,   # left rim (pivot zone top)
    0.55,   # mid-left descent
    0.00,   # cup low
    0.55,   # mid-right ascent
    0.95,   # right rim (just below left rim)
    0.75,   # handle low (upper-half of cup)
    0.90,   # latest price approaching pivot
])


def _pip_match_score(prices: np.ndarray, template: np.ndarray) -> float:
    """Score 0-1 for how closely a price segment's PIP-shape matches `template`.

    Extracts len(template) PIPs from prices, normalizes both to [0,1], and
    returns 1 - mean_abs_error (clipped). Length-invariant — handles bases of
    very different durations naturally.
    """
    if len(prices) < len(template) or len(template) < 3:
        return 0.0
    idxs = _pip_extract(prices, len(template))
    if len(idxs) < len(template):
        return 0.0
    sample = _pip_normalize(prices, idxs)
    if sample.size != template.size:
        return 0.0
    mae = float(np.mean(np.abs(sample - template)))
    return float(max(0.0, 1.0 - mae * 1.5))   # mae of 0.67 → score 0


# ─── Base / pivot detection ───────────────────────────────────────────────────

def _detect_base(df: pd.DataFrame) -> dict | None:
    """Find the most recent meaningful consolidation base.

    Strategy:
      1. Find the most recent swing high within 25% of the 52-week high.
      2. Group nearby swing highs within ±3% of that bar into a *pivot zone*
         (Bulkowski: "rims should be near the same price level but be flexible").
         The pivot price is the mean of the zone's highs — robust to a single
         spike-bar that's 1% above the rest.
      3. Base low = min low between earliest zone bar and last bar.

    Soft floors: depth slightly outside [2.5%, 35%] is still returned with a
    penalty applied downstream — only egregious violations (depth > 50% or
    < 1%) are hard-rejected. The 12% → 25% widening from 52w high catches
    textbook bases that form after normal corrections.
    """
    if len(df) < _MIN_BASE_DAYS + _SWING_WINDOW * 2:
        return None

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    last_idx = len(df) - 1

    high_52w = float(np.max(high[-min(252, len(df)):]))
    swing_idxs = _swing_highs(df)
    if not swing_idxs:
        return None

    # Find the most recent swing high near the 52w high
    candidate_idx: int | None = None
    for idx in reversed(swing_idxs):
        if last_idx - idx < _MIN_BASE_DAYS:
            continue
        if float(high[idx]) < high_52w * _BASE_NEAR_52W_PCT:
            continue
        candidate_idx = idx
        break

    if candidate_idx is None:
        return None

    candidate_price = float(high[candidate_idx])
    # Build the pivot zone: every swing high within ±3% of the candidate.
    zone_tol = candidate_price * (_PIVOT_ZONE_TOLERANCE_PCT / 100.0)
    zone_idxs = [
        idx for idx in swing_idxs
        if abs(float(high[idx]) - candidate_price) <= zone_tol
    ]
    if not zone_idxs:
        zone_idxs = [candidate_idx]

    earliest_zone_idx = min(zone_idxs)
    # Pivot price: average of the zone's swing highs (robust to single spike-bars)
    pivot_price = float(np.mean([high[i] for i in zone_idxs]))
    base_low = float(np.min(low[earliest_zone_idx : last_idx + 1]))
    base_length = last_idx - earliest_zone_idx
    if pivot_price <= 0:
        return None
    base_depth_pct = (pivot_price - base_low) / pivot_price * 100.0

    # Egregious hard floors only — borderline cases scored softly downstream
    if base_depth_pct > 50.0:
        return None
    if base_depth_pct < 1.0:
        return None

    return {
        "pivot_idx": earliest_zone_idx,
        "pivot_price": pivot_price,
        "base_low": base_low,
        "base_high": pivot_price,
        "base_length_days": base_length,
        "base_depth_pct": base_depth_pct,
        "zone_count": len(zone_idxs),
    }


# ─── Buyability scoring ───────────────────────────────────────────────────────

def _classify_buyability(
    current_price: float,
    pivot_price: float | None,
    ma_50: float | None,
    ma_200: float | None,
) -> tuple[Buyability, float | None]:
    """Compute (buyability, extension_pct).

    Order matters: broken > extended > at_pivot > in_base > no_pattern.
    """
    # Broken: a damaged uptrend is never buyable, regardless of base
    if ma_50 is not None and current_price < ma_50 * (1.0 + _BROKEN_BELOW_50MA_PCT / 100.0):
        return ("broken", None)
    if ma_200 is not None and current_price < ma_200:
        return ("broken", None)

    if pivot_price is None:
        return ("no_pattern", None)

    extension_pct = (current_price - pivot_price) / pivot_price * 100.0

    if extension_pct > _EXTENDED_THRESHOLD_PCT:
        return ("extended", extension_pct)
    if _AT_PIVOT_RANGE_PCT[0] <= extension_pct <= _AT_PIVOT_RANGE_PCT[1]:
        return ("at_pivot", extension_pct)
    if extension_pct < _AT_PIVOT_RANGE_PCT[0]:
        return ("in_base", extension_pct)
    return ("no_pattern", extension_pct)


# ─── Pattern detectors ────────────────────────────────────────────────────────
# Each returns a quality score 0-1 if it matches, else 0. A stock can match
# multiple patterns; the highest-quality match wins.


def _score_flat_base(df: pd.DataFrame, base: dict) -> float:
    """Flat base: ~5-15 weeks, shallow depth, after a prior advance.

    Soft-penalty version — no hard cliffs. Length, depth, and prior advance
    each contribute a 0-1 score; the worst metric drags the overall score
    down but doesn't zero it out.
    """
    length = base["base_length_days"]
    depth = base["base_depth_pct"]

    # Length: soft window 25-75 bars, gaussian falloff outside (50-bar half-life)
    length_score = _soft_window(length, lo=25, hi=75, falloff=0.6)

    # Depth: ideal ~5-12%, gentle falloff beyond. Above 18% the score decays.
    depth_score = _soft_window(depth, lo=3.0, hi=12.0, falloff=0.7)

    # Prior advance: ideal ≥30% in 60 days. Soft penalty below — a 20% advance
    # still scores ~0.7, a 10% advance ~0.4.
    pivot_idx = base["pivot_idx"]
    if pivot_idx >= 60:
        prior_close_now = float(df["close"].iloc[pivot_idx])
        prior_close_60_back = float(df["close"].iloc[pivot_idx - 60])
        if prior_close_60_back > 0:
            prior_advance = (prior_close_now - prior_close_60_back) / prior_close_60_back * 100.0
        else:
            prior_advance = 0.0
        # Sigmoid-ish: 0% → ~0.1, 30% → 0.85, 60% → ~1.0
        prior_score = float(1.0 / (1.0 + np.exp(-(prior_advance - 15.0) / 12.0)))
    else:
        prior_score = 0.4

    return float(np.clip(0.40 * depth_score + 0.30 * length_score + 0.30 * prior_score, 0.0, 1.0))


def _score_cup_with_handle(df: pd.DataFrame, base: dict) -> float:
    """Cup-with-Handle: U-shape consolidation with a small downward drift handle.

    Soft-penalty version + PIP shape match. Bulkowski says "be flexible" — so
    handle, depth, symmetry, and right-rim alignment are now all soft. A real
    CWH with a slightly deep handle or a marginally V-ish cup still scores.
    The PIP template comparison gives a length-independent shape score.
    """
    length = base["base_length_days"]
    depth = base["base_depth_pct"]
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1

    # Soft length window (35-325 bars = 7-65 weeks per Bulkowski)
    length_score = _soft_window(length, lo=35, hi=325, falloff=0.4)
    # Soft depth window (Bulkowski 38-62% of prior trend ≈ 15-30% of price typically;
    # widened to 8-40% with soft falloff)
    depth_score = _soft_window(depth, lo=8.0, hi=40.0, falloff=0.5)
    # Bail out if length is clearly wrong (e.g. 10 bars) — saves work
    if length < 20 or length > 400:
        return 0.0

    sub = df.iloc[pivot_idx : last_idx + 1]
    if len(sub) < 5:
        return 0.0
    low_idx_rel = int(sub["low"].to_numpy().argmin())
    low_idx = pivot_idx + low_idx_rel

    # Cup symmetry: ideal low at middle of base; soft penalty for off-center.
    cup_position = (low_idx - pivot_idx) / max(length, 1)
    symmetry_score = _gaussian(cup_position, center=0.5, width=0.30)

    # Right side recovery — soft. Full credit at ≥95% of left rim, decays below.
    days_since_low = last_idx - low_idx
    if days_since_low < 5:
        return 0.0   # not enough bars after the low to call it a recovery
    right_side = df.iloc[low_idx : last_idx + 1]
    right_high = float(right_side["high"].max())
    left_rim = float(sub["high"].iloc[: min(8, len(sub))].max())
    if left_rim <= 0:
        return 0.0
    rim_ratio = right_high / left_rim
    rim_score = float(np.clip((rim_ratio - 0.80) / 0.15, 0.0, 1.0))

    # Handle: now SOFT. Find the rightmost local high in the right side; the
    # handle is the trailing window. Missing handle → reduced score, not zero.
    right_high_rel = int(right_side["high"].to_numpy().argmax())
    right_high_idx = low_idx + right_high_rel
    handle_bars = last_idx - right_high_idx

    if handle_bars >= 2:
        handle = df.iloc[right_high_idx : last_idx + 1]
        handle_low = float(handle["low"].min())
        handle_high = float(handle["high"].max())
        handle_depth = (handle_high - handle_low) / handle_high * 100.0 if handle_high > 0 else 0.0

        # Handle depth: gaussian centered on 9%, width 6% (so 3-15% gets full credit-ish)
        handle_depth_score = _gaussian(handle_depth, center=9.0, width=6.0)
        # Handle bars: gaussian centered ~10, width 8 (3-25 well-supported)
        handle_bars_score = _gaussian(handle_bars, center=10.0, width=8.0)

        # Handle position: prefer upper half of cup, but degrade smoothly if not
        cup_low = float(sub["low"].iloc[low_idx_rel])
        cup_height = right_high - cup_low if right_high > cup_low else 1.0
        handle_position = (handle_low - cup_low) / cup_height if cup_height > 0 else 0.5
        handle_pos_score = float(np.clip((handle_position - 0.3) / 0.4, 0.0, 1.0))

        handle_vol = float(handle["volume"].mean())
        cup_vol = (float(df.iloc[pivot_idx:right_high_idx]["volume"].mean())
                   if right_high_idx > pivot_idx else handle_vol)
        if cup_vol > 0:
            vol_ratio = handle_vol / cup_vol
            # Drying = good. ratio of 0.5 → 1.0, ratio of 1.0 → 0.5, 1.3 → 0.1
            vol_dry_score = float(np.clip(1.5 - vol_ratio, 0.0, 1.0))
        else:
            vol_dry_score = 0.5

        handle_score = (
            0.35 * handle_depth_score +
            0.20 * handle_bars_score +
            0.25 * handle_pos_score +
            0.20 * vol_dry_score
        )
    else:
        # No handle yet — still a candidate, just downgraded.
        handle_score = 0.35

    # ── PIP template-match (shape-independent of base length) ─────────────────
    # Compare the smoothed close curve over the base to the CWH template PIPs.
    close_arr = df["close"].to_numpy()[pivot_idx : last_idx + 1]
    smoothed = _smooth(close_arr)
    pip_score = _pip_match_score(smoothed, _CWH_TEMPLATE_PIPS)

    return float(np.clip(
        0.15 * length_score +
        0.15 * depth_score +
        0.15 * symmetry_score +
        0.10 * rim_score +
        0.25 * handle_score +
        0.20 * pip_score,
        0.0, 1.0,
    ))


def _score_vcp(df: pd.DataFrame, base: dict) -> float:
    """Volatility Contraction Pattern: successive lower-high contractions.

    Soft-penalty version. Uses ATR-confirmed zigzag pivots for contraction
    detection (volatility-adaptive), and a single contraction is allowed (just
    scored lower than 2+).
    """
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1
    sub = df.iloc[pivot_idx : last_idx + 1].reset_index(drop=True)
    if len(sub) < 25:
        return 0.0

    # Prefer ATR-confirmed pivots, fall back to smoothed swings on short bases.
    pivots = _atr_zigzag(sub)
    if len(pivots) < 3:
        # Fallback: smoothed swings
        s_highs = _swing_highs(sub, window=3)
        s_lows = _swing_lows(sub, window=3)
        # Build alternating sequence by interleaving
        marked = sorted(
            [(i, "H") for i in s_highs] + [(i, "L") for i in s_lows],
            key=lambda x: x[0],
        )
        pivots = marked

    if len(pivots) < 3:
        return 0.0

    # Build alternating H-L pairs from the pivot sequence
    contractions: list[float] = []
    for i in range(len(pivots) - 1):
        a_idx, a_kind = pivots[i]
        b_idx, b_kind = pivots[i + 1]
        if a_kind == "H" and b_kind == "L":
            h_price = float(sub["high"].iloc[a_idx])
            l_price = float(sub["low"].iloc[b_idx])
            if h_price > 0:
                contractions.append((h_price - l_price) / h_price * 100.0)
    # Take the last 4 contractions, latest first
    contractions = contractions[-4:][::-1]

    if not contractions:
        return 0.0

    # Tightness of the latest contraction — gaussian centered on ~4%, generous.
    # A 10% contraction still scores ~0.5; a 2% scores 1.0.
    last_depth = contractions[0]
    tightness_score = _gaussian(last_depth, center=3.0, width=8.0)

    # Progression: each successive contraction should be tighter than the prior.
    # Award partial credit for partial tightening.
    if len(contractions) >= 2:
        progression = 0.0
        for i in range(len(contractions) - 1):
            prior = contractions[i + 1]
            curr = contractions[i]
            if prior <= 0:
                continue
            ratio = curr / prior
            # ratio < 0.75 → 1.0; ratio = 0.85 → 0.6; ratio = 1.0 → 0.2; > 1.1 → 0
            if ratio <= 0.75:
                progression += 1.0
            elif ratio <= 0.85:
                progression += 0.7
            elif ratio <= 0.95:
                progression += 0.4
            elif ratio <= 1.05:
                progression += 0.2
        progression /= max(len(contractions) - 1, 1)
    else:
        # Single contraction — partial credit, can't measure progression
        progression = 0.4

    # Volume drying — softer than before. ratio of 1.0 → 0.2, 0.7 → 0.85, 0.5 → 1.0.
    if len(sub) >= 40:
        recent_vol = float(sub["volume"].tail(20).mean())
        prior_vol = float(sub["volume"].iloc[-40:-20].mean())
        if prior_vol > 0:
            vol_ratio = recent_vol / prior_vol
            vol_score = float(np.clip(1.5 - vol_ratio, 0.0, 1.0))
        else:
            vol_score = 0.3
    else:
        vol_score = 0.4

    return float(np.clip(
        0.40 * progression + 0.35 * tightness_score + 0.25 * vol_score,
        0.0, 1.0,
    ))


def _score_high_tight_flag(df: pd.DataFrame) -> tuple[float, dict | None]:
    """High-Tight Flag: ~90-120%+ in 4-8 weeks, then sideways 3-5 weeks within ≤25%.

    Soft-penalty version. Thrust < 90% no longer hard-rejects — a 70% thrust
    still produces a respectable score, just lower. Rare-but-powerful pattern.
    """
    n = len(df)
    if n < 60:
        return (0.0, None)

    flag_lookback = 20
    flag_start_idx = n - flag_lookback
    flag_high = float(df["high"].iloc[flag_start_idx:].max())
    flag_low = float(df["low"].iloc[flag_start_idx:].min())
    if flag_high <= 0:
        return (0.0, None)
    flag_depth_pct = (flag_high - flag_low) / flag_high * 100.0

    # Egregious-violation early exit only
    if flag_depth_pct > 40.0:
        return (0.0, None)

    # Soft flag depth: gaussian centered on 12%, width 12 (5-25% well-rewarded)
    flag_tightness_score = _gaussian(flag_depth_pct, center=12.0, width=12.0)

    thrust_window_start = max(0, flag_start_idx - 40)
    thrust_low = float(df["low"].iloc[thrust_window_start:flag_start_idx].min())
    if thrust_low <= 0:
        return (0.0, None)
    thrust_pct = (flag_high - thrust_low) / thrust_low * 100.0

    # Thrust below 50% is not a HTF — but everything 50-200% scored softly.
    if thrust_pct < 50.0:
        return (0.0, None)
    # Sigmoid: 50% → 0.2, 90% → 0.65, 120% → 0.85, 200% → ~1.0
    thrust_score = float(1.0 / (1.0 + np.exp(-(thrust_pct - 90.0) / 25.0)))

    quality = float(np.clip(0.55 * thrust_score + 0.45 * flag_tightness_score, 0.0, 1.0))
    info = {
        "pivot_idx": flag_start_idx + flag_lookback - 1,
        "pivot_price": flag_high,
        "base_low": flag_low,
        "base_high": flag_high,
        "base_length_days": flag_lookback,
        "base_depth_pct": flag_depth_pct,
    }
    return (quality, info)


def _score_ascending_triangle(df: pd.DataFrame, base: dict) -> float:
    """Ascending Triangle: flat resistance line + rising lows converging toward it.

    Soft-penalty version. Resistance tightness, rising-lows count, and approach
    distance each contribute softly.
    """
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1
    sub = df.iloc[pivot_idx : last_idx + 1].reset_index(drop=True)
    if len(sub) < 25:
        return 0.0

    highs = _swing_highs(sub, window=4)
    lows = _swing_lows(sub, window=4)
    if len(highs) < 2 or len(lows) < 2:
        return 0.0

    recent_highs = [float(sub["high"].iloc[h]) for h in highs[-5:]]
    if len(recent_highs) < 2:
        return 0.0
    r_max, r_min = max(recent_highs), min(recent_highs)
    resistance_tightness = (r_max - r_min) / r_max * 100.0 if r_max > 0 else 100.0
    # Soft: 0% spread = 1.0, 4% = 0.6, 8% = 0.2
    resistance_score = float(np.clip(1.0 - resistance_tightness / 8.0, 0.0, 1.0))

    resistance_level = (r_max + r_min) / 2.0

    recent_lows = [float(sub["low"].iloc[l]) for l in lows[-4:]]
    if len(recent_lows) < 2:
        return 0.0
    rising_count = sum(
        1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i - 1]
    )
    rising_score = rising_count / max(len(recent_lows) - 1, 1)

    current = float(sub["close"].iloc[-1])
    dist_from_resistance = (resistance_level - current) / resistance_level * 100.0 if resistance_level > 0 else 100.0
    # Sweet spot 0-8% below resistance; beyond that, soft decay.
    if -2.0 <= dist_from_resistance <= 8.0:
        approach_score = 1.0 - abs(dist_from_resistance - 3.0) / 8.0
    else:
        approach_score = max(0.0, 1.0 - abs(dist_from_resistance - 3.0) / 15.0)
    approach_score = float(np.clip(approach_score, 0.0, 1.0))

    return float(np.clip(
        0.35 * resistance_score + 0.40 * rising_score + 0.25 * approach_score,
        0.0, 1.0,
    ))


def _score_three_weeks_tight(df: pd.DataFrame) -> tuple[float, dict | None]:
    """Three Weeks Tight (3WT): 3+ consecutive weekly closes tightly clustered.

    Soft-penalty version. Range > 4% no longer hard-rejects — a 5-6% range still
    produces a measurable (but lower) score.
    """
    n = len(df)
    if n < 60:
        return (0.0, None)

    closes = df["close"].to_numpy()
    lows = df["low"].to_numpy()
    n_weeks = n // 5
    if n_weeks < 5:
        return (0.0, None)

    weekly_closes = [float(closes[(w + 1) * 5 - 1]) for w in range(n_weeks) if (w + 1) * 5 <= n]
    weekly_lows = [float(np.min(lows[w * 5:(w + 1) * 5])) for w in range(n_weeks) if (w + 1) * 5 <= n]

    if len(weekly_closes) < 5:
        return (0.0, None)

    tight_window = weekly_closes[-4:]
    wc_max, wc_min = max(tight_window), min(tight_window)
    if wc_min <= 0:
        return (0.0, None)
    range_pct = (wc_max - wc_min) / wc_min * 100.0

    # Egregious cap only
    if range_pct > 10.0:
        return (0.0, None)
    # Soft tightness: 0% → 1.0, 2% → 0.8, 4% → 0.5, 6% → 0.25, 8% → 0.1
    tightness_score = float(np.exp(-(range_pct / 4.0) ** 2))

    if len(weekly_closes) >= 12:
        prior_close = weekly_closes[-12]
        advance_pct = (weekly_closes[-4] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0
        # Sigmoid: 0% → 0.1, 15% → 0.55, 30% → 0.85
        prior_score = float(1.0 / (1.0 + np.exp(-(advance_pct - 12.0) / 8.0)))
    else:
        prior_score = 0.4

    quality = float(np.clip(0.55 * tightness_score + 0.45 * prior_score, 0.0, 1.0))

    if quality < _QUALITY_FLOOR:
        return (0.0, None)

    pivot_price = wc_max
    base_low = min(weekly_lows[-4:])
    return (quality, {
        "pivot_idx": n - 20,
        "pivot_price": pivot_price,
        "base_low": base_low,
        "base_high": pivot_price,
        "base_length_days": 20,
        "base_depth_pct": range_pct,
    })


def _score_bull_flag(df: pd.DataFrame) -> tuple[float, dict | None]:
    """Bull Flag: a sharp advance followed by an orderly pullback on declining volume.

    Soft-penalty version. Pole 20-120%, flag depth 3-25%, retrace ≤50% are all
    softly bounded with gaussian / sigmoid falloff.
    """
    n = len(df)
    if n < 50:
        return (0.0, None)

    close = df["close"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    vol = df["volume"].to_numpy()

    flag_window = 15
    flag_start = n - flag_window
    flag_high = float(np.max(high[flag_start:]))
    flag_low = float(np.min(low[flag_start:]))
    flag_close = float(close[-1])
    if flag_high <= 0:
        return (0.0, None)

    flag_depth_pct = (flag_high - flag_low) / flag_high * 100.0
    # Egregious cap only
    if flag_depth_pct > 35.0:
        return (0.0, None)
    # Soft flag depth: gaussian centered on 11%, width 10
    tightness_score = _gaussian(flag_depth_pct, center=11.0, width=10.0)

    pole_window_end = flag_start
    pole_window_start = max(0, flag_start - 40)
    pole_low = float(np.min(low[pole_window_start:pole_window_end]))
    pole_high = float(np.max(high[pole_window_start:pole_window_end]))
    if pole_low <= 0:
        return (0.0, None)
    pole_advance_pct = (pole_high - pole_low) / pole_low * 100.0
    if pole_advance_pct < 15.0 or pole_advance_pct > 200.0:
        return (0.0, None)
    # Sigmoid: 15% → 0.2, 30% → 0.5, 50% → 0.75, 80%+ → ~0.95
    pole_score = float(1.0 / (1.0 + np.exp(-(pole_advance_pct - 35.0) / 15.0)))

    if (pole_high - pole_low) <= 0:
        return (0.0, None)
    retrace_pct = (pole_high - flag_low) / (pole_high - pole_low) * 100.0
    # Soft retrace: ideal ≤38% (Fibonacci), penalize beyond. 25% → 1.0, 38% → 0.85,
    # 50% → 0.5, 70% → 0.15
    retrace_score = float(np.clip(1.0 - max(0.0, retrace_pct - 25.0) / 40.0, 0.0, 1.0))

    flag_vol = float(np.mean(vol[flag_start:]))
    pole_vol = float(np.mean(vol[pole_window_start:pole_window_end]))
    if pole_vol > 0:
        vol_contraction = flag_vol / pole_vol
        vol_score = float(np.clip(1.5 - vol_contraction, 0.0, 1.0))
    else:
        vol_score = 0.3

    position_in_flag = ((flag_close - flag_low) / (flag_high - flag_low)
                       if (flag_high - flag_low) > 0 else 0.5)
    position_score = float(np.clip(position_in_flag, 0.0, 1.0))

    quality = float(np.clip(
        0.25 * pole_score +
        0.20 * tightness_score +
        0.15 * retrace_score +
        0.20 * vol_score +
        0.20 * position_score,
        0.0, 1.0,
    ))

    if quality < _QUALITY_FLOOR:
        return (0.0, None)

    return (quality, {
        "pivot_idx": flag_start,
        "pivot_price": flag_high,
        "base_low": flag_low,
        "base_high": flag_high,
        "base_length_days": flag_window,
        "base_depth_pct": flag_depth_pct,
    })


def _has_power_play_signal(df: pd.DataFrame) -> bool:
    """Power Play: breakout day (or recent) shows volume ≥ 2× average AND
    close in upper 25% of day's range. Indicates institutional accumulation.

    This is not a base pattern — it's added as a quality signal to the details
    dict of whichever base pattern is already detected.
    """
    if len(df) < 50:
        return False
    avg_vol = float(df["volume"].tail(50).mean())
    last_vol = float(df["volume"].iloc[-1])
    last_high = float(df["high"].iloc[-1])
    last_low  = float(df["low"].iloc[-1])
    last_close = float(df["close"].iloc[-1])
    if avg_vol <= 0 or (last_high - last_low) <= 0:
        return False
    volume_surge = last_vol >= avg_vol * 2.0
    strong_close = (last_close - last_low) / (last_high - last_low) >= 0.75
    return volume_surge and strong_close


# ─── Public entry point ───────────────────────────────────────────────────────

def detect_pattern(
    df: pd.DataFrame,
    *,
    ma_50: float | None = None,
    ma_200: float | None = None,
) -> PatternResult:
    """Detect the best-matching pattern + buyability for one stock.

    df must have columns: open, high, low, close, volume — newest bar last.
    Pass ma_50 and ma_200 if known (for the "broken" buyability check).
    """
    if df is None or df.empty or len(df) < _MIN_BASE_DAYS + _SWING_WINDOW * 2:
        return PatternResult()

    current_price = float(df["close"].iloc[-1])

    # ── Frozen-stock guard ───────────────────────────────────────────────────
    # Acquisition targets and halted stocks show essentially zero daily range.
    # Their ADR is far below any normally-trading stock. Detect and tag these
    # before any pattern scoring — they must never surface as "at pivot".
    recent = df.tail(20)
    avg_adr = float(
        ((recent["high"] - recent["low"]) / recent["close"]).mean() * 100
    )
    if avg_adr < _FROZEN_ADR_PCT:
        return PatternResult(
            buyability="frozen",
            extension_pct=None,
            details={"frozen": True, "avg_adr_pct": round(avg_adr, 3)},
        )

    # Try patterns that don't use the standard base detector first
    htf_q,  htf_info  = _score_high_tight_flag(df)
    tbt_q,  tbt_info  = _score_three_weeks_tight(df)
    flag_q, flag_info = _score_bull_flag(df)

    base = _detect_base(df)
    candidates: list[tuple[str, float, dict | None]] = []
    if base is not None:
        candidates.extend([
            ("flat_base",          _score_flat_base(df, base),           base),
            ("cwh",                _score_cup_with_handle(df, base),     base),
            ("vcp",                _score_vcp(df, base),                 base),
            ("ascending_triangle", _score_ascending_triangle(df, base),  base),
        ])
    if htf_q  > 0: candidates.append(("high_tight_flag",    htf_q,  htf_info))
    if tbt_q  > 0: candidates.append(("three_weeks_tight",  tbt_q,  tbt_info))
    if flag_q > 0: candidates.append(("bull_flag",          flag_q, flag_info))

    # Power play is a quality signal, not a standalone pattern — it boosts whichever
    # pattern wins, and is stored in the details dict for the UI to display.
    power_play = _has_power_play_signal(df)

    if not candidates:
        # Even with no pattern we can still classify buyability if we know the MAs
        buyability, ext_pct = _classify_buyability(current_price, None, ma_50, ma_200)
        return PatternResult(buyability=buyability, extension_pct=ext_pct)

    # Best match wins
    candidates.sort(key=lambda x: x[1], reverse=True)
    pattern_type, quality, info = candidates[0]

    if quality < _QUALITY_FLOOR:
        # Even the best match is weak — fall back to base info but no pattern label.
        # Lowered from 0.40 → 0.25 to match the soft-penalty regime: with cliffs
        # removed, the score itself is a meaningful filter and 0.25 catches the
        # genuinely weak candidates while keeping borderline-textbook setups.
        info = base or info
        if info is None:
            buyability, ext_pct = _classify_buyability(current_price, None, ma_50, ma_200)
            return PatternResult(buyability=buyability, extension_pct=ext_pct)
        pattern_type = "none"
        quality = 0.0

    pivot_price = info["pivot_price"] if info else None
    buyability, ext_pct = _classify_buyability(current_price, pivot_price, ma_50, ma_200)

    detail_dict: dict = {"all_scores": {c[0]: round(c[1], 3) for c in candidates}}
    if power_play:
        detail_dict["power_play"] = True

    return PatternResult(
        pattern_type=pattern_type,  # type: ignore[arg-type]
        quality=quality,
        pivot_price=pivot_price,
        base_low=info["base_low"] if info else None,
        base_high=info["base_high"] if info else None,
        base_length_days=info["base_length_days"] if info else None,
        base_depth_pct=info["base_depth_pct"] if info else None,
        extension_pct=ext_pct,
        buyability=buyability,
        details=detail_dict,
    )
