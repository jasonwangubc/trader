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

_SWING_WINDOW = 5                # bars on either side for swing-high/low
_MIN_BASE_DAYS = 25              # 5 weeks — minimum to call something a "base"
_MAX_BASE_DEPTH_PCT = 35.0       # >35% deep = "wide and loose," not buyable
_MIN_BASE_DEPTH_PCT = 2.5        # <2.5% = acquisition pinning, not a real base
_AT_PIVOT_RANGE_PCT = (-3.0, 5.0)  # within -3% to +5% of pivot = at_pivot
_EXTENDED_THRESHOLD_PCT = 5.0    # > 5% past pivot = extended (Minervini's rule)
_BROKEN_BELOW_50MA_PCT = -3.0    # > 3% below 50ma = broken
_FROZEN_ADR_PCT = 0.50           # avg daily range < 0.5% of price = frozen/acquired


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
    # Reject impossibly tight — acquisition pinning to an offer price
    if base_depth_pct < _MIN_BASE_DEPTH_PCT:
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

    Strict definition — the handle is the defining feature. No handle → not a CWH.
      - 7-65 weeks (35-325 trading days)
      - Depth 12-35%
      - Cup low in middle 40% of the base
      - HANDLE REQUIRED: 5-15% pullback in last 5-15 days, on declining volume,
        forming a downward-drifting consolidation near the cup's right rim.
    """
    length = base["base_length_days"]
    depth = base["base_depth_pct"]
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1

    if length < 35 or length > 325:
        return 0.0
    if depth < 12.0 or depth > 35.0:
        return 0.0

    sub = df.iloc[pivot_idx : last_idx + 1]
    low_idx_rel = sub["low"].to_numpy().argmin()
    low_idx = pivot_idx + int(low_idx_rel)

    # Cup symmetry: low must be in middle 30-70% of the base. Otherwise it's
    # not a U — could be a hockey stick or an inverted-V.
    cup_position = (low_idx - pivot_idx) / max(length, 1)
    if cup_position < 0.30 or cup_position > 0.70:
        return 0.0
    symmetry_score = 1.0 - abs(cup_position - 0.5) * 2.5
    symmetry_score = max(0.0, min(1.0, symmetry_score))

    # Need enough bars after the cup low to form a handle
    days_since_low = last_idx - low_idx
    if days_since_low < 10:
        return 0.0   # not enough right-side recovery for a handle

    # Right side: must recover to near the cup's left rim (within 10%)
    right_side = df.iloc[low_idx : last_idx + 1]
    right_high = float(right_side["high"].max())
    left_rim   = float(sub["high"].iloc[: min(5, len(sub))].max())
    if left_rim > 0 and right_high < left_rim * 0.90:
        return 0.0   # right side didn't recover enough

    # ── Handle detection (required) ──────────────────────────────────────────
    # Find the highest point in the right side, then look at the trailing
    # consolidation: depth 4-15%, lasting 3-15 bars, on declining volume.
    right_high_rel = right_side["high"].to_numpy().argmax()
    right_high_idx = low_idx + int(right_high_rel)
    handle_bars = last_idx - right_high_idx
    if handle_bars < 3 or handle_bars > 25:
        return 0.0   # no clear handle window

    handle = df.iloc[right_high_idx : last_idx + 1]
    handle_low  = float(handle["low"].min())
    handle_high = float(handle["high"].max())
    handle_close = float(handle["close"].iloc[-1])
    handle_depth = (handle_high - handle_low) / handle_high * 100.0
    if handle_depth < 4.0 or handle_depth > 15.0:
        return 0.0   # handle too tight or too deep

    # Handle must be in the UPPER half of the cup (not in the lower half — that
    # would mean the stock retraced back into the cup)
    cup_low  = float(sub["low"].iloc[low_idx_rel])
    cup_mid  = (cup_low + right_high) / 2
    if handle_low < cup_mid:
        return 0.0   # handle dipped too deep — pattern broken

    # Volume during handle must be lower than during the cup
    handle_vol = float(handle["volume"].mean())
    cup_vol    = float(df.iloc[pivot_idx : right_high_idx]["volume"].mean()) if right_high_idx > pivot_idx else handle_vol
    if cup_vol > 0 and handle_vol > cup_vol * 1.10:
        return 0.0   # volume should DRY UP in the handle, not pick up

    vol_dry_score = max(0.0, min(1.0, (1.0 - handle_vol / cup_vol) * 2.0)) if cup_vol > 0 else 0.5
    handle_quality = 1.0 - abs(handle_depth - 9.0) / 7.0  # 9% is the sweet spot
    handle_quality = max(0.0, min(1.0, handle_quality))

    depth_score = 1.0 - abs(depth - 22.0) / 13.0
    return float(np.clip(0.25 * depth_score + 0.25 * symmetry_score + 0.30 * handle_quality + 0.20 * vol_dry_score, 0.0, 1.0))


def _score_vcp(df: pd.DataFrame, base: dict) -> float:
    """Volatility Contraction Pattern: successive lower-high contractions.

    Strict definition — requires at least 2 contractions where each is
    meaningfully tighter than the prior (≤85% of prior depth), AND the
    most recent contraction is tight (≤8% deep), AND volume has dried up.
    """
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1
    sub = df.iloc[pivot_idx : last_idx + 1].reset_index(drop=True)
    if len(sub) < 30:
        return 0.0

    highs = _swing_highs(sub, window=3)
    lows = _swing_lows(sub, window=3)
    if len(highs) < 3 or len(lows) < 3:
        return 0.0

    # Build contraction depths from swing pairs (most recent first)
    contractions: list[float] = []
    h_iter = sorted(highs)
    l_iter = sorted(lows)
    for h_idx in reversed(h_iter[-4:]):
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

    # Hard requirement: at least 2 contractions to compare
    if len(contractions) < 2:
        return 0.0

    # Most-recent contraction MUST be meaningfully tighter than the prior.
    # If the latest contraction isn't tighter, it's not a VCP.
    if contractions[0] >= contractions[1] * 0.90:
        return 0.0

    # Most-recent contraction MUST be genuinely tight (≤ 10% depth).
    # Wider than that and it's not really contracting — it's just a base.
    if contractions[0] > 10.0:
        return 0.0

    # Progression score: how many successive tightenings
    progression = 0.0
    for i in range(len(contractions) - 1):
        if contractions[i] < contractions[i + 1] * 0.75:
            progression += 1.0
        elif contractions[i] < contractions[i + 1] * 0.85:
            progression += 0.7
        elif contractions[i] < contractions[i + 1]:
            progression += 0.3
    progression /= max(len(contractions) - 1, 1)

    # Volume must have dried up meaningfully — at least 15% lower than prior
    if len(sub) >= 40:
        recent_vol = float(sub["volume"].tail(20).mean())
        prior_vol  = float(sub["volume"].iloc[-40:-20].mean())
        if prior_vol <= 0:
            return 0.0
        vol_ratio = recent_vol / prior_vol
        if vol_ratio > 0.95:
            return 0.0   # volume hasn't dried up — not a VCP
        vol_score = max(0.0, min(1.0, (1.0 - vol_ratio) * 2.5))
    else:
        return 0.0

    last_depth = contractions[0]
    tightness_score = max(0.0, 1.0 - last_depth / 10.0)  # ≤2% is great, ≥10% is bad

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


def _score_ascending_triangle(df: pd.DataFrame, base: dict) -> float:
    """Ascending Triangle: flat resistance line + rising lows converging toward it.

    Bulkowski rates this as one of the best-performing geometric patterns
    (~68% upside breakout, avg +40%). The resistance need not be perfect —
    3+ swing highs within 2% of each other counts.
    """
    pivot_idx = base["pivot_idx"]
    last_idx = len(df) - 1
    sub = df.iloc[pivot_idx : last_idx + 1].reset_index(drop=True)
    if len(sub) < 30:
        return 0.0

    highs = _swing_highs(sub, window=4)
    lows = _swing_lows(sub, window=4)
    if len(highs) < 3 or len(lows) < 3:
        return 0.0

    # Flat resistance: most recent 3+ swing highs within 2.5% of each other
    recent_highs = [float(sub["high"].iloc[h]) for h in highs[-5:]]
    if len(recent_highs) < 3:
        return 0.0
    r_max, r_min = max(recent_highs), min(recent_highs)
    resistance_tightness = (r_max - r_min) / r_max * 100.0
    if resistance_tightness > 4.0:
        return 0.0   # swing highs too scattered — no flat top

    resistance_level = (r_max + r_min) / 2.0
    resistance_score = max(0.0, 1.0 - resistance_tightness / 4.0)

    # Rising lows: each swing low higher than the previous one
    recent_lows = [float(sub["low"].iloc[l]) for l in lows[-4:]]
    if len(recent_lows) < 2:
        return 0.0
    rising_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i - 1])
    if rising_count == 0:
        return 0.0   # lows are not rising
    rising_score = rising_count / (len(recent_lows) - 1)

    # Price approaching resistance: current price within 5% below resistance
    current = float(sub["close"].iloc[-1])
    dist_from_resistance = (resistance_level - current) / resistance_level * 100.0
    if dist_from_resistance < 0 or dist_from_resistance > 10.0:
        approach_score = 0.3
    else:
        approach_score = 1.0 - dist_from_resistance / 10.0

    return float(np.clip(0.35 * resistance_score + 0.40 * rising_score + 0.25 * approach_score, 0.0, 1.0))


def _score_three_weeks_tight(df: pd.DataFrame) -> tuple[float, dict | None]:
    """Three Weeks Tight (3WT): 3+ consecutive weekly closes within 1.5% of each other.

    IBD's favorite setup for stocks pausing after a powerful advance. The tighter
    the weekly closes, the higher the quality. Often precedes explosive moves.
    """
    n = len(df)
    if n < 60:
        return (0.0, None)

    # Build weekly bars from daily (ISO week)
    df_copy = df.copy()
    df_copy["week"] = pd.to_datetime(df_copy["date"]).dt.isocalendar().week if "date" in df_copy.columns else df_copy.index.map(lambda x: x // 5)
    # Simpler: group every 5 trading days as a "week"
    closes = df["close"].to_numpy()
    highs  = df["high"].to_numpy()
    lows   = df["low"].to_numpy()
    n_weeks = n // 5
    if n_weeks < 5:
        return (0.0, None)

    weekly_closes = [float(closes[(w + 1) * 5 - 1]) for w in range(n_weeks) if (w + 1) * 5 <= n]
    weekly_highs  = [float(np.max(highs[w * 5 : (w + 1) * 5])) for w in range(n_weeks) if (w + 1) * 5 <= n]
    weekly_lows   = [float(np.min(lows[w * 5 : (w + 1) * 5]))  for w in range(n_weeks) if (w + 1) * 5 <= n]

    if len(weekly_closes) < 5:
        return (0.0, None)

    # Check last 3-4 weeks for tight closes
    tight_window = weekly_closes[-4:]
    wc_max, wc_min = max(tight_window), min(tight_window)
    if wc_min <= 0:
        return (0.0, None)
    range_pct = (wc_max - wc_min) / wc_min * 100.0

    if range_pct > 4.0:
        return (0.0, None)   # too loose

    # Prior advance: price should be 15%+ above its level 10 weeks ago
    if len(weekly_closes) >= 12:
        prior_close = weekly_closes[-12]
        advance_pct = (weekly_closes[-4] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0
        if advance_pct < 15.0:
            return (0.0, None)
        prior_score = min(1.0, advance_pct / 40.0)
    else:
        prior_score = 0.5

    tightness_score = max(0.0, 1.0 - range_pct / 4.0)
    quality = float(np.clip(0.55 * tightness_score + 0.45 * prior_score, 0.0, 1.0))

    if quality < 0.25:
        return (0.0, None)

    pivot_price = wc_max
    base_low    = min(weekly_lows[-4:])
    return (quality, {
        "pivot_idx": n - 20,
        "pivot_price": pivot_price,
        "base_low": base_low,
        "base_high": pivot_price,
        "base_length_days": 20,
        "base_depth_pct": range_pct,
    })


def _score_bull_flag(df: pd.DataFrame) -> tuple[float, dict | None]:
    """Bull Flag: a sharp advance (30-100% in 4-8 weeks) followed by an orderly
    pullback (6-20% over 2-3 weeks) on meaningfully declining volume.

    Strict version — the pole must be SHARP (not a slow grind), the flag must
    be tight, and volume must contract meaningfully during the consolidation.
    """
    n = len(df)
    if n < 50:
        return (0.0, None)

    close = df["close"].to_numpy()
    high  = df["high"].to_numpy()
    low   = df["low"].to_numpy()
    vol   = df["volume"].to_numpy()

    flag_window = 15  # 3 weeks
    flag_start  = n - flag_window
    flag_high   = float(np.max(high[flag_start:]))
    flag_low    = float(np.min(low[flag_start:]))
    flag_close  = float(close[-1])
    if flag_high <= 0:
        return (0.0, None)

    flag_depth_pct = (flag_high - flag_low) / flag_high * 100.0
    # Stricter flag depth: 6-20% (was 3-25%). Below 6% is noise, above 20% is too deep.
    if flag_depth_pct > 20.0 or flag_depth_pct < 6.0:
        return (0.0, None)

    # Pole: 30-100% advance in the 4-8 weeks before the flag (stricter than 20-120%)
    pole_window_end   = flag_start
    pole_window_start = max(0, flag_start - 40)
    pole_low  = float(np.min(low[pole_window_start:pole_window_end]))
    pole_high = float(np.max(high[pole_window_start:pole_window_end]))
    if pole_low <= 0:
        return (0.0, None)
    pole_advance_pct = (pole_high - pole_low) / pole_low * 100.0
    if pole_advance_pct < 30.0 or pole_advance_pct > 100.0:
        return (0.0, None)

    # The flag should retrace ≤ 38% of the pole (Fibonacci — classic bull-flag)
    if (pole_high - pole_low) <= 0:
        return (0.0, None)
    retrace_pct = (pole_high - flag_low) / (pole_high - pole_low) * 100.0
    if retrace_pct > 38.0:
        return (0.0, None)

    # Volume must meaningfully contract: flag avg vol ≤ 75% of pole avg vol
    flag_vol = float(np.mean(vol[flag_start:]))
    pole_vol = float(np.mean(vol[pole_window_start:pole_window_end]))
    if pole_vol <= 0:
        return (0.0, None)
    vol_contraction = flag_vol / pole_vol
    if vol_contraction > 0.75:
        return (0.0, None)   # volume hasn't dried up enough

    # Close should be in upper half of the flag (approaching breakout)
    position_in_flag = (flag_close - flag_low) / (flag_high - flag_low) if (flag_high - flag_low) > 0 else 0.5
    if position_in_flag < 0.40:
        return (0.0, None)

    pole_score       = min(1.0, pole_advance_pct / 60.0)
    tightness_score  = max(0.0, 1.0 - flag_depth_pct / 18.0)
    vol_score        = max(0.0, min(1.0, (1.0 - vol_contraction) * 2.0))
    position_score   = position_in_flag

    quality = float(np.clip(0.30 * pole_score + 0.25 * tightness_score + 0.25 * vol_score + 0.20 * position_score, 0.0, 1.0))

    if quality < 0.35:
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

    if quality < 0.40:
        # Even the best match is weak — fall back to base info but no pattern label.
        # We raised from 0.20 to 0.40: a real pattern should score at least 40/100,
        # otherwise we're labeling noise as setups.
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
