"""Point-in-time feature extraction for the ML setup ranker.

This is the parity-critical module: the Phase-1 backtest scan (historical
candidates) and run_screener (tonight's candidates) both call
extract_features() on the same kind of inputs, so the model sees identical
feature semantics at training time and at serving time.

Hard rules:
  - Pure numpy/pandas. No DB access, no ML-library imports.
  - Everything is computed from bars up to AND INCLUDING the signal bar
    (`hist`, and `spy_hist` cut to the same calendar date). Nothing here may
    look at bars after the signal date.
  - Output values are native float/int/None (JSON-serializable for the JSONB
    candidate column). None = missing; LightGBM handles NaN natively.
  - Any change to feature names or semantics requires bumping FEATURE_VERSION;
    training filters out rows whose stored "fv" doesn't match.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from app.services.pattern_service import PatternResult
from app.services.regime_service import _count_distribution_days
from app.services.trend_template import TTResult
from app.services.vcp_scorer import VCPResult

log = logging.getLogger(__name__)

FEATURE_VERSION = 1

# Stable integer codes for the categorical pattern feature (LightGBM categorical).
# Vocabulary must match pattern_service.PatternType / screener PATTERN_EV_MULT.
PATTERN_TYPE_CODES: dict[str, int] = {
    "vcp": 0,
    "cwh": 1,
    "flat_base": 2,
    "high_tight_flag": 3,
    "ascending_triangle": 4,
    "three_weeks_tight": 5,
    "bull_flag": 6,
    "none": 7,
}
CATEGORICAL_FEATURES = ["pattern_type_code"]

FEATURE_NAMES: list[str] = [
    # Trend / price structure
    "close_vs_ma50_pct", "close_vs_ma150_pct", "close_vs_ma200_pct",
    "ma50_vs_ma200_pct", "ma200_slope_21d_pct",
    "pct_off_52w_high", "pct_above_52w_low", "bars_since_52w_high",
    "log10_close", "tt_score",
    # Momentum
    "ret_5d", "ret_10d", "ret_21d", "ret_63d", "ret_126d", "ret_252d",
    # Relative strength vs SPY
    "rs_vs_spy_21d", "rs_vs_spy_63d", "rs_vs_spy_126d", "rs_vs_spy_252d",
    # Volatility / volume
    "atr_pct", "adr20_pct", "atr_ratio_20", "range_tightness_10d",
    "close_pos_20d", "vol_ratio_20_60", "up_down_vol_ratio_50",
    "last_vol_vs_50d", "log10_dollar_vol_20d",
    # Pattern
    "pattern_type_code", "pattern_quality", "is_at_pivot",
    "base_depth_pct", "base_length_days", "extension_pct",
    "pivot_dist_atr", "zone_count", "power_play",
    "pat_score_vcp", "pat_score_cwh", "pat_score_flat_base",
    "pat_score_asc_triangle", "pat_score_htf", "pat_score_3wt",
    "pat_score_bull_flag",
    # VCP scorer
    "vcp_score", "vcp_tightness", "vcp_compression", "vcp_volume",
    "vcp_pivot", "vcp_trend", "vcp_atr_ratio", "vcp_volume_ratio",
    "vcp_base_depth_pct", "vcp_n_contractions", "vcp_last_contraction_pct",
    # CWH internals
    "cwh_handle_depth_pct", "cwh_handle_bars", "cwh_handle_pos",
    "cwh_handle_vol_ratio",
    # Market regime (SPY)
    "spy_vs_ma200_pct", "spy_vs_ma50_pct", "spy_ma200_slope_21d_pct",
    "spy_ret_21d", "spy_ret_63d", "spy_dist_days_25", "spy_realized_vol_21d",
]


def _f(x) -> float | None:
    """Native rounded float, or None for missing/NaN/inf."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, 6)


def _i(x) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def atr_scalar(df: pd.DataFrame, period: int = 14) -> float:
    """Simple mean true range over the last `period` bars (scalar).

    Shared by the Phase-1 scan and run_screener so the stop distance and the
    ATR-based features use one definition.
    """
    if len(df) < period + 1:
        return 0.0
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)  # noqa: E741
    c = df["close"].values.astype(float)
    trs = []
    for i in range(-period, 0):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        trs.append(tr)
    return float(np.mean(trs))


def _pct(a: float, b: float) -> float | None:
    """(a/b - 1) * 100, None-safe."""
    if b is None or b <= 0 or a is None:
        return None
    return (a / b - 1.0) * 100.0


def _ret(closes: np.ndarray, k: int) -> float | None:
    if len(closes) <= k or closes[-1 - k] <= 0:
        return None
    return (closes[-1] / closes[-1 - k] - 1.0) * 100.0


def extract_features(
    hist: pd.DataFrame,
    *,
    spy_hist: pd.DataFrame | None,
    tt: TTResult,
    vcp: VCPResult,
    pat: PatternResult,
    atr: float,
) -> dict:
    """Compute the v1 feature dict for one candidate at its signal bar.

    hist: bars up to and including the signal bar (columns open/high/low/
    close/volume, ascending). spy_hist: SPY bars cut to the same calendar
    date (or None). tt/vcp/pat: scorer outputs already computed on `hist`.
    atr: atr_scalar(hist) at the signal bar.
    """
    closes = hist["close"].to_numpy(dtype=float)
    highs = hist["high"].to_numpy(dtype=float)
    lows = hist["low"].to_numpy(dtype=float)
    vols = hist["volume"].to_numpy(dtype=float)
    n = len(closes)
    close = closes[-1]

    out: dict = {"fv": FEATURE_VERSION}

    # ── Trend / price structure ───────────────────────────────────────────────
    ma50 = float(np.mean(closes[-50:])) if n >= 50 else None
    ma150 = float(np.mean(closes[-150:])) if n >= 150 else None
    ma200 = float(np.mean(closes[-200:])) if n >= 200 else None
    ma200_21ago = float(np.mean(closes[-221:-21])) if n >= 221 else None

    out["close_vs_ma50_pct"] = _f(_pct(close, ma50))
    out["close_vs_ma150_pct"] = _f(_pct(close, ma150))
    out["close_vs_ma200_pct"] = _f(_pct(close, ma200))
    out["ma50_vs_ma200_pct"] = _f(_pct(ma50, ma200)) if ma50 and ma200 else None
    out["ma200_slope_21d_pct"] = _f(_pct(ma200, ma200_21ago)) if ma200 and ma200_21ago else None

    w52 = closes[-min(252, n):]
    hi52 = float(np.max(w52))
    lo52 = float(np.min(w52))
    out["pct_off_52w_high"] = _f(_pct(close, hi52))
    out["pct_above_52w_low"] = _f(_pct(close, lo52))
    out["bars_since_52w_high"] = _i(len(w52) - 1 - int(np.argmax(w52)))
    out["log10_close"] = _f(math.log10(close)) if close > 0 else None
    out["tt_score"] = _i(tt.score)

    # ── Momentum ──────────────────────────────────────────────────────────────
    for k, name in ((5, "ret_5d"), (10, "ret_10d"), (21, "ret_21d"),
                    (63, "ret_63d"), (126, "ret_126d"), (252, "ret_252d")):
        out[name] = _f(_ret(closes, k))

    # ── Relative strength vs SPY (point-in-time — never tt.rs_raw) ───────────
    spy_closes = None
    if spy_hist is not None and not spy_hist.empty:
        spy_closes = spy_hist["close"].to_numpy(dtype=float)
    for k, name in ((21, "rs_vs_spy_21d"), (63, "rs_vs_spy_63d"),
                    (126, "rs_vs_spy_126d"), (252, "rs_vs_spy_252d")):
        stock_r = _ret(closes, k)
        spy_r = _ret(spy_closes, k) if spy_closes is not None else None
        out[name] = _f(stock_r - spy_r) if stock_r is not None and spy_r is not None else None

    # ── Volatility / volume ───────────────────────────────────────────────────
    out["atr_pct"] = _f(atr / close * 100.0) if atr and close > 0 else None
    if n >= 20:
        out["adr20_pct"] = _f(float(np.mean((highs[-20:] - lows[-20:]) / closes[-20:])) * 100.0)
        rng_hi, rng_lo = float(np.max(highs[-20:])), float(np.min(lows[-20:]))
        out["close_pos_20d"] = _f((close - rng_lo) / (rng_hi - rng_lo)) if rng_hi > rng_lo else None
    else:
        out["adr20_pct"] = None
        out["close_pos_20d"] = None
    atr_20ago = atr_scalar(hist.iloc[:-20]) if n >= 35 else 0.0
    out["atr_ratio_20"] = _f(atr / atr_20ago) if atr and atr_20ago > 0 else None
    out["range_tightness_10d"] = (
        _f((float(np.max(highs[-10:])) - float(np.min(lows[-10:]))) / close * 100.0)
        if n >= 10 and close > 0 else None
    )
    if n >= 60:
        v60 = float(np.mean(vols[-60:]))
        out["vol_ratio_20_60"] = _f(float(np.mean(vols[-20:])) / v60) if v60 > 0 else None
    else:
        out["vol_ratio_20_60"] = None
    if n >= 51:
        chg = np.diff(closes[-51:])
        v = vols[-50:]
        up_vol = float(np.sum(v[chg > 0]))
        down_vol = float(np.sum(v[chg < 0]))
        out["up_down_vol_ratio_50"] = _f(min(up_vol / down_vol, 10.0)) if down_vol > 0 else None
    else:
        out["up_down_vol_ratio_50"] = None
    if n >= 50:
        v50 = float(np.mean(vols[-50:]))
        out["last_vol_vs_50d"] = _f(vols[-1] / v50) if v50 > 0 else None
    else:
        out["last_vol_vs_50d"] = None
    if n >= 20:
        dv = float(np.mean(closes[-20:] * vols[-20:]))
        out["log10_dollar_vol_20d"] = _f(math.log10(dv)) if dv > 0 else None
    else:
        out["log10_dollar_vol_20d"] = None

    # ── Pattern ───────────────────────────────────────────────────────────────
    ptype = pat.pattern_type or "none"
    out["pattern_type_code"] = PATTERN_TYPE_CODES.get(ptype, PATTERN_TYPE_CODES["none"])
    out["pattern_quality"] = _f(pat.quality)
    out["is_at_pivot"] = 1 if pat.buyability == "at_pivot" else 0
    out["base_depth_pct"] = _f(pat.base_depth_pct)
    out["base_length_days"] = _i(pat.base_length_days)
    out["extension_pct"] = _f(pat.extension_pct)
    out["pivot_dist_atr"] = (
        _f((float(pat.pivot_price) - close) / atr)
        if pat.pivot_price is not None and atr and atr > 0 else None
    )
    details = pat.details or {}
    out["zone_count"] = _i(details.get("zone_count"))
    out["power_play"] = 1 if details.get("power_play") else 0

    all_scores = details.get("all_scores") or {}
    out["pat_score_vcp"] = _f(all_scores.get("vcp", 0.0))
    out["pat_score_cwh"] = _f(all_scores.get("cwh", 0.0))
    out["pat_score_flat_base"] = _f(all_scores.get("flat_base", 0.0))
    out["pat_score_asc_triangle"] = _f(all_scores.get("ascending_triangle", 0.0))
    out["pat_score_htf"] = _f(all_scores.get("high_tight_flag", 0.0))
    out["pat_score_3wt"] = _f(all_scores.get("three_weeks_tight", 0.0))
    out["pat_score_bull_flag"] = _f(all_scores.get("bull_flag", 0.0))

    # ── VCP scorer ────────────────────────────────────────────────────────────
    vd = vcp.details or {}
    out["vcp_score"] = _f(vcp.score)
    out["vcp_tightness"] = _f(vd.get("tightness"))
    out["vcp_compression"] = _f(vd.get("compression"))
    out["vcp_volume"] = _f(vd.get("volume"))
    out["vcp_pivot"] = _f(vd.get("pivot"))
    out["vcp_trend"] = _f(vd.get("trend"))
    out["vcp_atr_ratio"] = _f(vcp.atr_ratio)
    out["vcp_volume_ratio"] = _f(vcp.volume_ratio)
    out["vcp_base_depth_pct"] = _f(vcp.base_depth_pct)
    vm = details.get("vcp_metrics") or {}
    out["vcp_n_contractions"] = _i(vm.get("n_contractions"))
    out["vcp_last_contraction_pct"] = _f(vm.get("last_contraction_pct"))

    # ── CWH internals ─────────────────────────────────────────────────────────
    cm = details.get("cwh_metrics") or {}
    out["cwh_handle_depth_pct"] = _f(cm.get("handle_depth_pct"))
    out["cwh_handle_bars"] = _i(cm.get("handle_bars"))
    out["cwh_handle_pos"] = _f(cm.get("handle_position"))
    out["cwh_handle_vol_ratio"] = _f(cm.get("handle_vol_ratio"))

    # ── Market regime (SPY, point-in-time) ────────────────────────────────────
    if spy_closes is not None and len(spy_closes) >= 2:
        sn = len(spy_closes)
        spy_close = spy_closes[-1]
        spy_ma200 = float(np.mean(spy_closes[-200:])) if sn >= 200 else None
        spy_ma50 = float(np.mean(spy_closes[-50:])) if sn >= 50 else None
        spy_ma200_21ago = float(np.mean(spy_closes[-221:-21])) if sn >= 221 else None
        out["spy_vs_ma200_pct"] = _f(_pct(spy_close, spy_ma200)) if spy_ma200 else None
        out["spy_vs_ma50_pct"] = _f(_pct(spy_close, spy_ma50)) if spy_ma50 else None
        out["spy_ma200_slope_21d_pct"] = (
            _f(_pct(spy_ma200, spy_ma200_21ago)) if spy_ma200 and spy_ma200_21ago else None
        )
        out["spy_ret_21d"] = _f(_ret(spy_closes, 21))
        out["spy_ret_63d"] = _f(_ret(spy_closes, 63))
        dist_count, _status = _count_distribution_days(spy_hist)
        out["spy_dist_days_25"] = _i(dist_count)
        if sn >= 22:
            daily = np.diff(spy_closes[-22:]) / spy_closes[-22:-1]
            out["spy_realized_vol_21d"] = _f(float(np.std(daily)) * math.sqrt(252) * 100.0)
        else:
            out["spy_realized_vol_21d"] = None
    else:
        out["spy_vs_ma200_pct"] = None
        out["spy_vs_ma50_pct"] = None
        out["spy_ma200_slope_21d_pct"] = None
        out["spy_ret_21d"] = None
        out["spy_ret_63d"] = None
        out["spy_dist_days_25"] = None
        out["spy_realized_vol_21d"] = None

    return out


def to_matrix(rows: list[dict], feature_names: list[str] | None = None) -> np.ndarray:
    """Ordered float matrix from feature dicts; None/missing → NaN."""
    names = feature_names or FEATURE_NAMES
    mat = np.full((len(rows), len(names)), np.nan, dtype=float)
    for r, row in enumerate(rows):
        for c, name in enumerate(names):
            v = row.get(name)
            if v is not None:
                mat[r, c] = float(v)
    return mat
