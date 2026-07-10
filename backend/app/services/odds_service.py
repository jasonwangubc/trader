"""Empirical outcome odds for a trade plan, from the backtest signal cache.

Answers: "of historical setups similar to this one, what fraction hit my
target before my stop?" The plan's actual prices are converted into the
backtest engine's parameters (stop as a multiple of ATR, target as an
R-multiple of risk) and replayed over the cached two-year signal scan with
the exact same walk-forward logic the backtester uses.

Honesty constraints, surfaced as caveats in the result:
- The cache conditions on pattern / tier / quality / trend-template score
  only. Relative-strength rank, market regime, and sector are NOT stored
  historically, so the cohort is blind to them (documented limitation of the
  scan cache — see backtest_service._classify_tier notes).
- These are base rates of a population, not a forecast for this stock.
- If too few similar setups exist, the cohort is widened stepwise and the
  result says so.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.backtest_service import _classify_tier, simulate_from_candidates
from app.services.signal_scan_service import latest_successful_scan

log = logging.getLogger(__name__)

MIN_COHORT_TRADES = 30


@dataclass
class OddsEstimate:
    available: bool
    reason: str | None = None          # why unavailable (when available=False)

    cohort: str = ""                   # human description of the similar-setup filter
    widened: bool = False              # True if we had to relax similarity for sample size
    tier: str = ""                     # tier of THIS setup (S/A/B or "")

    n_setups: int = 0                  # similar historical signals found
    n_triggered: int = 0               # of those, how many actually broke out
    trigger_rate: float = 0.0

    target_pct: float = 0.0            # fraction of triggered trades hitting target first
    stop_pct: float = 0.0
    time_pct: float = 0.0              # timed out (neither hit within time stop)
    avg_r: float = 0.0                 # average R-multiple across triggered trades
    time_avg_r: float = 0.0            # average R of the time-stop exits

    stop_atr: float = 0.0              # the plan's stop expressed in ATR multiples
    target_r: float = 0.0              # the plan's target expressed in R
    time_stop_days: int = 0

    scan_date: str | None = None       # when the underlying signal scan ran
    caveats: list[str] = field(default_factory=list)


def _unavailable(reason: str) -> OddsEstimate:
    return OddsEstimate(available=False, reason=reason)


async def compute_outcome_odds(
    session: AsyncSession,
    *,
    pattern_type: str | None,
    buyability: str | None,
    pattern_quality: float | None,
    entry_price: float,
    stop_price: float,
    target_price: float,
    atr: float,
    time_stop_days: int = 20,
) -> OddsEstimate:
    """Empirical P(target before stop) for this plan, from similar cached setups.

    Uses the newest successful scan regardless of its lookback (504-day and
    1260-day scans are both valid cohort sources; freshest wins).
    """
    if atr <= 0:
        return _unavailable("No ATR available for this symbol.")
    risk = entry_price - stop_price
    if risk <= 0:
        return _unavailable("Stop must be below entry.")
    reward = target_price - entry_price
    if reward <= 0:
        return _unavailable("Target must be above entry.")

    scan = await latest_successful_scan(session)
    if scan is None:
        return _unavailable(
            "No backtest signal scan cached yet — run a backtest (Phase 1 scan) "
            "on the Backtest page first."
        )

    stop_atr = round(risk / atr, 2)
    target_r = round(reward / risk, 2)

    quality = float(pattern_quality) if pattern_quality is not None else 0.0
    tier = _classify_tier(pattern_type or "", buyability or "", quality)

    # Similarity levels, tightest first. Widen until the cohort has enough
    # triggered trades to mean anything.
    levels: list[tuple[str, dict]] = []
    if pattern_type:
        q_lo, q_hi = max(0.0, quality - 0.15), min(1.0, quality + 0.15)
        levels.append((
            f"{pattern_type} setups with quality {q_lo:.2f}–{q_hi:.2f}",
            {"pattern_types": {pattern_type}, "quality_range": (q_lo, q_hi),
             "pattern_quality_min": 0.0},
        ))
    if tier:
        levels.append((
            f"all Tier {tier} setups",
            {"tiers": {tier}, "pattern_quality_min": 0.0},
        ))
    if buyability in ("at_pivot", "in_base"):
        levels.append((
            f"all {buyability.replace('_', ' ')} setups with quality ≥ 0.50",
            {"buyabilities": {buyability}, "pattern_quality_min": 0.50},
        ))
    levels.append((
        "all cached setups with quality ≥ 0.50",
        {"pattern_quality_min": 0.50},
    ))

    chosen = None
    for idx, (label, filters) in enumerate(levels):
        result = await simulate_from_candidates(
            session,
            scan_id=scan.id,
            tt_min=4,
            stop_atr=stop_atr,
            target_r=target_r,
            time_stop=time_stop_days,
            lookback_days=scan.lookback_days,
            **filters,
        )
        if result.total_trades >= MIN_COHORT_TRADES:
            chosen = (idx, label, result)
            break
        if chosen is None or result.total_trades > chosen[2].total_trades:
            chosen = (idx, label, result)

    idx, label, result = chosen
    triggered = [t for t in result.trades if t.triggered]
    n_triggered = len(triggered)
    n_setups = len(result.trades)
    if n_triggered == 0:
        return _unavailable(f"No similar historical setups triggered ({label}).")

    by_reason = {"target": 0, "stop": 0, "time": 0}
    time_rs: list[float] = []
    for t in triggered:
        by_reason[t.exit_reason or "time"] = by_reason.get(t.exit_reason or "time", 0) + 1
        if t.exit_reason == "time" and t.r_multiple is not None:
            time_rs.append(t.r_multiple)

    caveats = [
        "Cohort conditioned on pattern/tier/quality/trend-template only — "
        "RS rank, market regime, and sector aren't in the historical cache.",
        f"Your stop was mapped to {stop_atr:.2f}×ATR and target to "
        f"{target_r:.2f}R for the replay — approximations of your exact prices.",
        "Base rates of a historical population, not a prediction for this stock.",
    ]
    if n_triggered < MIN_COHORT_TRADES:
        caveats.insert(0, f"Small sample ({n_triggered} triggered trades) — low confidence.")

    return OddsEstimate(
        available=True,
        cohort=label,
        widened=idx > 0 and bool(pattern_type),
        tier=tier,
        n_setups=n_setups,
        n_triggered=n_triggered,
        trigger_rate=round(n_triggered / n_setups, 4) if n_setups else 0.0,
        target_pct=round(by_reason["target"] / n_triggered, 4),
        stop_pct=round(by_reason["stop"] / n_triggered, 4),
        time_pct=round(by_reason["time"] / n_triggered, 4),
        avg_r=result.avg_r,
        time_avg_r=round(sum(time_rs) / len(time_rs), 3) if time_rs else 0.0,
        stop_atr=stop_atr,
        target_r=target_r,
        time_stop_days=time_stop_days,
        scan_date=(scan.finished_at or scan.started_at).strftime("%Y-%m-%d")
        if (scan.finished_at or scan.started_at) else None,
        caveats=caveats,
    )
