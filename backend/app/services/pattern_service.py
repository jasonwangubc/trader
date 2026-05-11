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


Buyability = Literal["at_pivot", "in_base", "extended", "broken", "no_pattern"]
PatternType = Literal["vcp", "cwh", "flat_base", "high_tight_flag", "none"]


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

_SWING_WINDOW = 5                # bars on either side for swing-high/low
_MIN_BASE_DAYS = 25              # 5 weeks — minimum to call something a "base"
_MAX_BASE_DEPTH_PCT = 35.0       # >35% deep = "wide and loose," not buyable
_AT_PIVOT_RANGE_PCT = (-3.0, 5.0)  # within -3% to +5% of pivot = at_pivot
_EXTENDED_THRESHOLD_PCT = 5.0    # > 5% past pivot = extended (Minervini's rule)
_BROKEN_BELOW_50MA_PCT = -3.0    # > 3% below 50ma = broken


# ─── Swing point detection ────────────────────────────────────────────────────

def _swing_highs(df: pd.DataFrame, window: int = _SWING_WINDOW) -> list[int]:
    """Return indices of swing highs (local maxima in `window` bars on each side)."""
    out: list[int] = []
    n = len(df)
    if n < 2 * window + 1:
        return out
    high = df["high"].to_numpy()
    for i in range(window, n - window):
        if high[i] == high[i - window : i + window + 1].max():
            out.append(i)
    return out


def _swing_lows(df: pd.DataFrame, window: int = _SWING_WINDOW) -> list[int]:
    out: list[int] = []
    n = len(df)
    if n < 2 * window + 1:
        return out
    low = df["low"].to_numpy()
    for i in range(window, n - window):
        if low[i] == low[i - window : i + window + 1].min():
            out.append(i)
    return out


# ─── Base / pivot detection ───────────────────────────────────────────────────

def _detect_base(df: pd.DataFrame) -> dict | None:
    """Find the most recent meaningful consolidation base.

    Strategy: walk back from the last bar to find the most recent swing high
    that's near the 52-week high. That high is the candidate pivot. The base
    is the region between that pivot and now, reaching down to the lowest low
    in between. Reject if too short, too deep, or trending wrong direction.
    """
    if len(df) < _MIN_BASE_DAYS + _SWING_WINDOW * 2:
        return None

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    last_idx = len(df) - 1

    high_52w = float(np.max(high[-min(252, len(df)):]))
    swing_idxs = _swing_highs(df)
    if not swing_idxs:
        return None

    # Walk swing highs back; the candidate pivot is the most recent one near
    # the 52-week high (within 12%) that's at least _MIN_BASE_DAYS old.
    candidate_idx: int | None = None
    for idx in reversed(swing_idxs):
        if last_idx - idx < _MIN_BASE_DAYS:
            continue
        pivot_high = float(high[idx])
        # The pivot must be near recent highs to count as a base
        if pivot_high < high_52w * 0.85:
            continue
        candidate_idx = idx
        break

    if candidate_idx is None:
        return None

    pivot_price = float(high[candidate_idx])
    base_low = float(np.min(low[candidate_idx : last_idx + 1]))
    base_length = last_idx - candidate_idx
    base_depth_pct = (pivot_price - base_low) / pivot_price * 100.0

    # Reject "wide and loose" — Minervini doesn't trade these
    if base_depth_pct > _MAX_BASE_DEPTH_PCT:
        return None

    return {
        "pivot_idx": candidate_idx,
        "pivot_price": pivot_price,
        "base_low": base_low,
        "base_high": pivot_price,
        "base_length_days": base_length,
        "base_depth_pct": base_depth_pct,
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
    """Flat base: 5-15 weeks, depth ≤ 15%, after a prior advance.

    The defining quality: tightness. The flatter and tighter, the better.
    """
    length = base["base_length_days"]
    depth = base["base_depth_pct"]

    # Length window (5-15 weeks ≈ 25-75 trading days)
    if length < 25 or length > 75:
        return 0.0
    if depth > 15.0:
        return 0.0

    # Tighter is better. 5% depth = 1.0, 15% = 0.0 linearly
    depth_score = max(0.0, 1.0 - (depth - 5.0) / 10.0)
    # Mid-length window is best — not too short, not too long
    length_score = 1.0 - abs((length - 50) / 50.0) * 0.5

    # Require prior advance: 30%+ in the 60 days BEFORE the base
    pivot_idx = base["pivot_idx"]
    if pivot_idx >= 60:
        prior_close_now = float(df["close"].iloc[pivot_idx])
        prior_close_60_back = float(df["close"].iloc[pivot_idx - 60])
        prior_advance = (prior_close_now - prior_close_60_back) / prior_close_60_back * 100.0
        if prior_advance < 30.0:
            return 0.0
        prior_score = min(1.0, prior_advance / 60.0)  # 60% advance = full credit
    else:
        prior_score = 0.5

    return float(np.clip(0.4 * depth_score + 0.3 * length_score + 0.3 * prior_score, 0.0, 1.0))


def _score_cup_with_handle(df: pd.DataFrame, base: dict) -> float:
    """Cup-with-Handle: U-shape consolidation with a small downward drift handle.

    Recognition cues:
      - 7-65 weeks (35-325 trading days)
      - Depth 12-35%
      - Right side reaches near the left lip
      - A "handle" — final small pullback (5-15%) on lower volume, 1-3 weeks
    """
    length = base["base_length_days"]
    depth = base["base_depth_pct"]
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1

    if length < 35 or length > 325:
        return 0.0
    if depth < 12.0 or depth > 35.0:
        return 0.0

    # The "cup": find the lowest-low between pivot and now
    sub = df.iloc[pivot_idx : last_idx + 1]
    low_idx_rel = sub["low"].to_numpy().argmin()
    low_idx = pivot_idx + int(low_idx_rel)

    # Cup symmetry: low should be roughly in the middle
    cup_position = (low_idx - pivot_idx) / max(length, 1)
    if cup_position < 0.25 or cup_position > 0.75:
        symmetry_score = 0.3
    else:
        symmetry_score = 1.0 - abs(cup_position - 0.5) * 2.0

    # Handle: in the last 5-15 days, price should pull back 5-15% from a recent peak
    # within the right side of the cup
    if last_idx - low_idx < 10:
        handle_score = 0.3   # not enough recovery for a handle
    else:
        right_side = df.iloc[low_idx : last_idx + 1]
        recent_window = df.iloc[max(low_idx, last_idx - 20) : last_idx + 1]
        right_high = float(right_side["high"].max())
        recent_low = float(recent_window["low"].min())
        recent_close = float(recent_window["close"].iloc[-1])
        handle_depth = (right_high - recent_low) / right_high * 100.0
        if 4.0 <= handle_depth <= 16.0 and recent_close >= recent_low * 1.005:
            handle_score = 1.0 - abs(handle_depth - 9.0) / 7.0  # 9% is sweet spot
        else:
            handle_score = 0.4

    depth_score = 1.0 - abs(depth - 22.0) / 13.0  # 22% is the canonical cup depth
    return float(np.clip(0.35 * depth_score + 0.30 * symmetry_score + 0.35 * handle_score, 0.0, 1.0))


def _score_vcp(df: pd.DataFrame, base: dict) -> float:
    """Volatility Contraction Pattern: successive lower-high contractions.

    Look at the last 3 swing-high → swing-low contractions inside the base
    and check that each is tighter than the prior. Volume should also dry up.
    """
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1
    sub = df.iloc[pivot_idx : last_idx + 1].reset_index(drop=True)
    if len(sub) < 25:
        return 0.0

    highs = _swing_highs(sub, window=3)
    lows = _swing_lows(sub, window=3)
    if len(highs) < 3 or len(lows) < 3:
        return 0.0

    # Build a series of contraction depths (most recent 3)
    contractions: list[float] = []
    h_iter = sorted(highs)
    l_iter = sorted(lows)
    for h_idx in reversed(h_iter[-4:]):
        # Find the next swing low after this high
        following = [l for l in l_iter if l > h_idx]
        if not following:
            continue
        l_idx = following[0]
        h_price = float(sub["high"].iloc[h_idx])
        l_price = float(sub["low"].iloc[l_idx])
        depth = (h_price - l_price) / h_price * 100.0
        contractions.append(depth)
        if len(contractions) >= 3:
            break

    if len(contractions) < 2:
        return 0.0

    # We want each newer contraction to be smaller than the prior
    # contractions list is most-recent-first
    progression = 0.0
    for i in range(len(contractions) - 1):
        if contractions[i] < contractions[i + 1] * 0.85:
            progression += 1.0
        elif contractions[i] < contractions[i + 1]:
            progression += 0.5
    progression /= max(len(contractions) - 1, 1)

    # Volume contraction: recent 20 days vs prior 20 days
    if len(sub) >= 40:
        recent_vol = float(sub["volume"].tail(20).mean())
        prior_vol = float(sub["volume"].iloc[-40:-20].mean())
        if prior_vol > 0:
            vol_ratio = recent_vol / prior_vol
            vol_score = max(0.0, min(1.0, (1.0 - vol_ratio) * 2.0))
        else:
            vol_score = 0.5
    else:
        vol_score = 0.5

    # Tightness of the most-recent contraction
    last_depth = contractions[0]
    tightness_score = max(0.0, 1.0 - last_depth / 15.0)  # ≤5% is great, ≥15% is bad

    return float(np.clip(0.45 * progression + 0.30 * tightness_score + 0.25 * vol_score, 0.0, 1.0))


def _score_high_tight_flag(df: pd.DataFrame) -> tuple[float, dict | None]:
    """High-Tight Flag: 90-120%+ in 4-8 weeks, then sideways 3-5 weeks within ≤25%.

    Bypasses normal base detection — this pattern doesn't form a "base" in the
    usual sense; it's a tight flag riding a vertical move. Rare but powerful
    when it works.
    """
    n = len(df)
    if n < 60:
        return (0.0, None)

    close = df["close"].to_numpy()

    # Look at last 15-25 days for the flag (consolidation period)
    flag_lookback = 20
    flag_start_idx = n - flag_lookback
    flag_high = float(df["high"].iloc[flag_start_idx:].max())
    flag_low = float(df["low"].iloc[flag_start_idx:].min())
    flag_depth_pct = (flag_high - flag_low) / flag_high * 100.0
    if flag_depth_pct > 25.0:
        return (0.0, None)
    if flag_depth_pct < 5.0:
        # Too tight — probably noise or no real move
        return (0.0, None)

    # Prior thrust: 4-8 weeks before the flag, look for 90%+ advance
    thrust_window_start = max(0, flag_start_idx - 40)
    thrust_low = float(df["low"].iloc[thrust_window_start:flag_start_idx].min())
    if thrust_low <= 0:
        return (0.0, None)
    thrust_pct = (flag_high - thrust_low) / thrust_low * 100.0

    if thrust_pct < 90.0:
        return (0.0, None)
    thrust_score = min(1.0, thrust_pct / 150.0)
    flag_tightness_score = 1.0 - abs(flag_depth_pct - 12.0) / 13.0

    quality = float(np.clip(0.6 * thrust_score + 0.4 * flag_tightness_score, 0.0, 1.0))
    info = {
        "pivot_idx": flag_start_idx + flag_lookback - 1,
        "pivot_price": flag_high,
        "base_low": flag_low,
        "base_high": flag_high,
        "base_length_days": flag_lookback,
        "base_depth_pct": flag_depth_pct,
    }
    return (quality, info)


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

    # Try High-Tight Flag first — it doesn't use the standard base detector
    htf_q, htf_info = _score_high_tight_flag(df)

    base = _detect_base(df)
    candidates: list[tuple[str, float, dict | None]] = []
    if base is not None:
        candidates.extend([
            ("flat_base", _score_flat_base(df, base), base),
            ("cwh",       _score_cup_with_handle(df, base), base),
            ("vcp",       _score_vcp(df, base), base),
        ])
    if htf_q > 0:
        candidates.append(("high_tight_flag", htf_q, htf_info))

    if not candidates:
        # Even with no pattern we can still classify buyability if we know the MAs
        buyability, ext_pct = _classify_buyability(current_price, None, ma_50, ma_200)
        return PatternResult(buyability=buyability, extension_pct=ext_pct)

    # Best match wins
    candidates.sort(key=lambda x: x[1], reverse=True)
    pattern_type, quality, info = candidates[0]

    if quality < 0.20:
        # Even the best match is weak — fall back to base info but no pattern label
        info = base or info
        if info is None:
            buyability, ext_pct = _classify_buyability(current_price, None, ma_50, ma_200)
            return PatternResult(buyability=buyability, extension_pct=ext_pct)
        pattern_type = "none"
        quality = 0.0

    pivot_price = info["pivot_price"] if info else None
    buyability, ext_pct = _classify_buyability(current_price, pivot_price, ma_50, ma_200)

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
        details={"all_scores": {c[0]: round(c[1], 3) for c in candidates}},
    )
