"""Stop and target recommendations + Monte Carlo probability estimates.

Stop methods:
  ATR stop   — 1.5× ATR(14) below entry. The most widely used; adapts to
               each stock's actual volatility rather than a fixed %.
  Base-low   — 1% below the lowest low in the VCP base. The "invalidation"
               level — if the base fails, the setup thesis is wrong.
  Tight ATR  — 0.75× ATR for late-stage VCPs where the base is already very
               tight (base depth < 8%).

Target method: R-multiples (T1 = 1.5R, T2 = 2.5R, T3 = 4R).

Probability model: log-normal Monte Carlo (10 000 paths).
  Uses each symbol's actual daily return distribution (last 252 bars):
    μ = mean daily log return  (captures directional momentum)
    σ = std  daily log return  (captures volatility)
  Simulates N price paths and counts how often target is hit before stop
  within the given holding period. Positive drift (stage-2 uptrend) skews
  probabilities toward targets — which is exactly what Minervini screens for.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_N_PATHS = 10_000
_RNG     = np.random.default_rng(0)


@dataclass
class StopOption:
    method: str
    price: float
    distance_pct: float   # % below entry
    description: str


@dataclass
class Target:
    label: str
    r_multiple: float
    price: float
    p_20d: float     # probability of hitting target before stop within 20 trading days
    p_40d: float     # same, 40 days


@dataclass
class Recommendations:
    symbol: str
    entry_price: float

    stops: list[StopOption] = field(default_factory=list)
    recommended_stop: StopOption | None = None

    targets: list[Target] = field(default_factory=list)

    atr_14: float = 0.0
    base_low: float | None = None
    daily_vol: float = 0.0           # daily log-return std
    annual_vol_pct: float = 0.0      # annualised vol in %
    daily_drift: float = 0.0         # mean daily log return
    expected_value_20d: float = 0.0  # EV of T2 trade at 20d horizon


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float(df["close"].iloc[-1]) * 0.02
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    trs = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
           for i in range(-period, 0)]
    return float(np.mean(trs))


def _base_low(df: pd.DataFrame, window: int = 50) -> float | None:
    """Lowest low in the last `window` bars (the current base)."""
    if df.empty:
        return None
    lows = df["low"].values.astype(float)[-window:]
    return float(np.min(lows)) if len(lows) > 0 else None


def _mc_probability(
    entry: float,
    stop: float,
    target: float,
    mu: float,       # daily log-return drift
    sigma: float,    # daily log-return std
    days: int,
) -> float:
    """Monte Carlo: P(target hit before stop within `days` trading days)."""
    if sigma <= 0 or stop >= entry or target <= entry:
        return 0.0

    # Generate random log-returns for all paths at once (fast)
    eps = _RNG.normal(mu, sigma, (_N_PATHS, days))
    log_prices = np.cumsum(eps, axis=1)
    prices = entry * np.exp(log_prices)          # shape (N_PATHS, days)

    target_crossed = np.any(prices >= target, axis=1)
    stop_crossed   = np.any(prices <= stop,   axis=1)

    # First-crossing index (days if never crossed)
    def first_cross_idx(mask_2d: np.ndarray, crossed_1d: np.ndarray) -> np.ndarray:
        idx = np.full(_N_PATHS, days)
        for i in np.where(crossed_1d)[0]:
            col = np.argmax(mask_2d[i])  # first True
            idx[i] = col
        return idx

    t_idx = first_cross_idx(prices >= target, target_crossed)
    s_idx = first_cross_idx(prices <= stop,   stop_crossed)

    # Count paths where target hit first
    target_first = int(np.sum(
        target_crossed & (~stop_crossed | (t_idx < s_idx))
    ))
    return round(target_first / _N_PATHS, 4)


def compute_recommendations(df: pd.DataFrame, symbol: str) -> Recommendations:
    """Compute stop/target recommendations from historical daily bars."""
    if df.empty or len(df) < 20:
        return Recommendations(symbol=symbol, entry_price=0.0)

    closes  = df["close"].values.astype(float)
    entry   = float(closes[-1])          # last close = assumed entry
    atr     = _atr(df)
    b_low   = _base_low(df)

    # ── Daily return stats (last 252 bars for drift + vol) ─────────────────
    hist = np.minimum(252, len(closes))
    log_rets = np.diff(np.log(closes[-hist:]))
    mu    = float(np.mean(log_rets))
    sigma = float(np.std(log_rets))
    ann_vol = sigma * np.sqrt(252) * 100   # annualised %

    rec = Recommendations(
        symbol=symbol,
        entry_price=round(entry, 2),
        atr_14=round(atr, 2),
        base_low=round(b_low, 2) if b_low else None,
        daily_vol=round(sigma, 5),
        annual_vol_pct=round(ann_vol, 1),
        daily_drift=round(mu, 5),
    )

    # ── Stop options ───────────────────────────────────────────────────────
    stops: list[StopOption] = []

    # 1. Standard ATR stop (1.5×)
    atr_stop_price = entry - 1.5 * atr
    stops.append(StopOption(
        method="ATR (1.5×)",
        price=round(atr_stop_price, 2),
        distance_pct=round((entry - atr_stop_price) / entry * 100, 1),
        description=f"1.5 × ATR({atr:.2f}) below entry. Adapts to {symbol}'s actual volatility. Most flexible.",
    ))

    # 2. Tight ATR (0.75×) for very tight VCPs
    tight_stop = entry - 0.75 * atr
    stops.append(StopOption(
        method="ATR (0.75× tight)",
        price=round(tight_stop, 2),
        distance_pct=round((entry - tight_stop) / entry * 100, 1),
        description="Tighter stop for late-stage VCPs where the base is already very tight. Lower risk but higher chance of being shaken out.",
    ))

    # 3. Base-low stop — only useful if there's a recognisable nearby base
    if b_low and b_low < entry:
        bl_stop = b_low * 0.99
        bl_dist_pct = (entry - bl_stop) / entry * 100
        # Only show if the base low is within a reasonable range (4-15%).
        # Outside that range the stock has either no clear base or has moved
        # so far from the base that it's irrelevant as an invalidation level.
        if 3.0 <= bl_dist_pct <= 15.0:
            stops.append(StopOption(
                method="Base low (VCP invalidation)",
                price=round(bl_stop, 2),
                distance_pct=round(bl_dist_pct, 1),
                description=f"1% below the recent base low ({b_low:.2f}). Thesis-based: if the base breaks, exit. Best for committed VCP trades.",
            ))

    # Choose recommended stop (prefer ATR unless base-low is meaningfully tighter)
    if b_low and b_low < entry:
        bl_stop_val = b_low * 0.99
        atr_pct  = (entry - atr_stop_price) / entry
        base_pct = (entry - bl_stop_val) / entry
        # Prefer base-low if it's within 50% wider than ATR stop
        rec.recommended_stop = stops[2] if base_pct <= atr_pct * 1.5 else stops[0]
    else:
        rec.recommended_stop = stops[0]
    rec.stops = stops

    # ── Targets (R-multiple based) ──────────────────────────────────────────
    best_stop = rec.recommended_stop.price
    risk      = entry - best_stop
    if risk <= 0:
        return rec

    targets: list[Target] = []
    for label, mult in [("T1 (+1.5R)", 1.5), ("T2 (+2.5R)", 2.5), ("T3 (+4R)", 4.0)]:
        t_price = entry + risk * mult
        p20 = _mc_probability(entry, best_stop, t_price, mu, sigma, 20)
        p40 = _mc_probability(entry, best_stop, t_price, mu, sigma, 40)
        targets.append(Target(
            label=label,
            r_multiple=mult,
            price=round(t_price, 2),
            p_20d=p20,
            p_40d=p40,
        ))

    rec.targets = targets

    # ── Expected value at 20 days using T2 ─────────────────────────────────
    if targets:
        t2 = targets[1]
        p_win  = t2.p_20d
        p_lose = _mc_probability(best_stop, t2.price, best_stop, mu, sigma, 20)
        # EV = P(hit T2 first) × 2.5R + P(hit stop first) × -1R
        ev = p_win * t2.r_multiple - (1 - p_win - (1 - p_win - _mc_probability(
            entry, best_stop, entry * 10, mu, sigma, 20
        ))) * 1.0
        rec.expected_value_20d = round(ev, 3)

    return rec
