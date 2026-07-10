"""Empirical odds engine: plan-price conversion + cohort outcome fractions.

Seeds three synthetic symbols with hand-built bars — one hits the target,
one hits the stop, one times out — and asserts the odds come back 1/3 each.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from app.db.models import BacktestSignalCandidate, BacktestSignalScan, DailyBar
from app.services import signal_scan_service
from app.services.odds_service import compute_outcome_odds

LOOKBACK = 504
SIGNAL_BAR = 10
PIVOT = 100.0
ATR = 5.0


def _bar(symbol: str, day_idx: int, o: float, h: float, low: float, c: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        bar_date=datetime(2025, 1, 6) + timedelta(days=day_idx),
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=1_000_000,
        adj_close=Decimal(str(c)),
    )


def _seed_symbol_bars(session, symbol: str, outcome: str) -> None:
    """Bars 0-10 flat at ~90; bar 11 triggers the breakout (high >= 100,
    open exactly 100 so entry == pivot); then the scripted outcome.

    With the test plan (entry 100, stop 95, target 110): stop = 1.0 x ATR(5),
    target = 2.0R.
    """
    bars: list[DailyBar] = []
    for i in range(SIGNAL_BAR + 1):  # bars 0..10
        bars.append(_bar(symbol, i, 90, 91, 89, 90))
    bars.append(_bar(symbol, 11, 100, 101, 99, 100.5))  # trigger bar

    if outcome == "target":
        bars.append(_bar(symbol, 12, 102, 105, 101, 104))
        bars.append(_bar(symbol, 13, 105, 111, 104, 110.5))  # high >= 110 -> target
        for i in range(14, 40):
            bars.append(_bar(symbol, i, 110, 112, 109, 111))
    elif outcome == "stop":
        bars.append(_bar(symbol, 12, 99, 100, 97, 98))
        bars.append(_bar(symbol, 13, 97, 98, 94, 95.5))       # low <= 95 -> stop
        for i in range(14, 40):
            bars.append(_bar(symbol, i, 96, 97, 95.5, 96))
    else:  # time-out: meander between stop and target for > time_stop bars
        for i in range(12, 40):
            bars.append(_bar(symbol, i, 102, 106, 99, 103))

    session.add_all(bars)


async def _seed_scan(session) -> uuid.UUID:
    scan = BacktestSignalScan(
        lookback_days=LOOKBACK,
        symbols_scanned=3,
        candidate_count=3,
        status="success",
        finished_at=datetime(2025, 3, 1),
    )
    session.add(scan)
    await session.flush()
    for symbol in ("TGT", "STP", "TIM"):
        session.add(
            BacktestSignalCandidate(
                scan_id=scan.id,
                symbol=symbol,
                signal_date=datetime(2025, 1, 16),
                bar_index=SIGNAL_BAR,
                tt_score=6,
                vcp_score=Decimal("0.700"),
                pattern_type="vcp",
                pattern_quality=Decimal("0.700"),
                buyability="at_pivot",
                pivot_price=Decimal(str(PIVOT)),
                atr_at_signal=Decimal(str(ATR)),
            )
        )
    await session.flush()
    await session.commit()
    return scan.id


async def test_outcome_fractions_hand_computed(db_session):
    signal_scan_service._bars_cache.clear()  # module-global cache, test isolation
    _seed_symbol_bars(db_session, "TGT", "target")
    _seed_symbol_bars(db_session, "STP", "stop")
    _seed_symbol_bars(db_session, "TIM", "time")
    await _seed_scan(db_session)

    odds = await compute_outcome_odds(
        db_session,
        pattern_type="vcp",
        buyability="at_pivot",
        pattern_quality=0.70,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        atr=ATR,
    )

    assert odds.available
    assert odds.stop_atr == 1.0    # (100-95)/5
    assert odds.target_r == 2.0    # (110-100)/(100-95)
    assert odds.n_setups == 3
    assert odds.n_triggered == 3
    assert odds.trigger_rate == 1.0
    assert abs(odds.target_pct - 1 / 3) < 1e-3
    assert abs(odds.stop_pct - 1 / 3) < 1e-3
    assert abs(odds.time_pct - 1 / 3) < 1e-3
    assert odds.cohort.startswith("vcp")
    assert not odds.widened
    assert odds.tier == "A"        # at_pivot + quality 0.70
    assert any("Small sample" in c for c in odds.caveats)
    assert odds.scan_date == "2025-03-01"


async def test_invalid_plans_are_unavailable(db_session):
    for stop, target, atr, expect in [
        (105.0, 110.0, 5.0, "Stop must be below entry"),
        (95.0, 99.0, 5.0, "Target must be above entry"),
        (95.0, 110.0, 0.0, "No ATR"),
    ]:
        odds = await compute_outcome_odds(
            db_session,
            pattern_type="vcp",
            buyability="at_pivot",
            pattern_quality=0.7,
            entry_price=100.0,
            stop_price=stop,
            target_price=target,
            atr=atr,
        )
        assert not odds.available
        assert expect in (odds.reason or "")


async def test_no_scan_is_unavailable(db_session):
    signal_scan_service._bars_cache.clear()
    odds = await compute_outcome_odds(
        db_session,
        pattern_type="vcp",
        buyability="at_pivot",
        pattern_quality=0.7,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        atr=5.0,
    )
    assert not odds.available
    assert "No backtest signal scan" in (odds.reason or "")


async def test_odds_use_any_lookback_scan(db_session):
    """The odds card must find the newest successful scan regardless of its
    lookback — a 1260-day (5y) scan with no 504 scan present still serves."""
    signal_scan_service._bars_cache.clear()
    _seed_symbol_bars(db_session, "TGT", "target")
    _seed_symbol_bars(db_session, "STP", "stop")
    _seed_symbol_bars(db_session, "TIM", "time")

    scan = BacktestSignalScan(
        lookback_days=1260,
        symbols_scanned=3,
        candidate_count=3,
        status="success",
        finished_at=datetime(2025, 3, 1),
    )
    db_session.add(scan)
    await db_session.flush()
    for symbol in ("TGT", "STP", "TIM"):
        db_session.add(
            BacktestSignalCandidate(
                scan_id=scan.id,
                symbol=symbol,
                signal_date=datetime(2025, 1, 16),
                bar_index=SIGNAL_BAR,
                tt_score=6,
                vcp_score=Decimal("0.700"),
                pattern_type="vcp",
                pattern_quality=Decimal("0.700"),
                buyability="at_pivot",
                pivot_price=Decimal(str(PIVOT)),
                atr_at_signal=Decimal(str(ATR)),
            )
        )
    await db_session.commit()

    odds = await compute_outcome_odds(
        db_session,
        pattern_type="vcp",
        buyability="at_pivot",
        pattern_quality=0.70,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        atr=ATR,
    )
    assert odds.available
    assert odds.n_triggered == 3
