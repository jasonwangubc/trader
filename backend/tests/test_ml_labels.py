"""Trade-sim conventions + label builder.

simulate_candidate is the single source of truth for entry/exit mechanics
(shared by the Phase-2 backtest, the odds card, and training labels), so its
conventions are pinned here with hand-computed numbers. build_labels adds
date-realignment, cooldown, censoring, and forward returns on top.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np

from app.db.models import BacktestSignalCandidate, BacktestSignalScan, DailyBar
from app.services import signal_scan_service
from app.services.backtest_service import simulate_candidate
from app.services.ml_labels import build_labels

PIVOT = 100.0
ATR = 5.0
# Plan used throughout: stop 1×ATR below entry, 2R target, generous windows.
PLAN = dict(stop_atr=1.0, target_r=2.0, time_stop=20, trigger_window=10)


def _arrays(bars: list[tuple[float, float, float, float]]):
    """bars = [(open, high, low, close), ...] → numpy arrays."""
    o, h, l, c = zip(*bars, strict=True)  # noqa: E741
    return (np.array(o, float), np.array(h, float), np.array(l, float), np.array(c, float))


def _flat(n: int) -> list[tuple[float, float, float, float]]:
    return [(90, 91, 89, 90)] * n


# ─── simulate_candidate conventions (pure) ────────────────────────────────────


def test_next_day_trigger_and_pivot_fill():
    # Signal at bar 2; bar 3 opens at 99 and tags 101 intraday → buy-stop
    # fills at the pivot, not the open.
    bars = _flat(3) + [(99, 101, 98, 100.5)] + _flat(30)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.triggered and sim.days_to_trigger == 1
    assert sim.entry_price == 100.0          # max(open 99, pivot 100)
    assert sim.stop_price == 95.0
    assert sim.target_price == 110.0


def test_gap_up_fills_at_open_not_pivot():
    bars = _flat(3) + [(105, 107, 104, 106)] + _flat(30)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.entry_price == 105.0          # gap-up: you pay the open


def test_gap_through_stop_exits_at_open():
    # Entry 100 (stop 95); two bars later the open gaps to 92.
    bars = _flat(3) + [(100, 101, 99, 100.5), (98, 99, 96, 97), (92, 93, 90, 91)] + _flat(20)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.exit_reason == "stop"
    assert sim.exit_price == 92.0            # worse than the stop — gap slippage
    assert sim.r_multiple == (92.0 - 100.0) / 5.0


def test_intraday_stop_touch_exits_at_stop():
    bars = _flat(3) + [(100, 101, 99, 100.5), (97, 98, 94, 96)] + _flat(20)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.exit_reason == "stop"
    assert sim.exit_price == 95.0
    assert sim.r_multiple == -1.0


def test_same_bar_stop_and_target_is_conservative():
    # One wild bar touches both 95 and 110 → the stop wins by convention.
    bars = _flat(3) + [(100, 101, 99, 100.5), (100, 112, 94, 105)] + _flat(20)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.exit_reason == "stop"
    assert sim.exit_price == 95.0


def test_mae_mfe_hand_computed_including_trigger_bar():
    # Trigger bar low 97 (below entry, before any exit bar) must count in MAE.
    bars = _flat(3) + [
        (100, 101, 97, 100.5),   # trigger bar: entry 100, low 97
        (101, 104, 100, 103),
        (104, 111, 103, 110),    # high 111 ≥ target 110 → exit at 110
    ] + _flat(20)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN, record_excursions=True)
    assert sim.exit_reason == "target"
    assert sim.r_multiple == 2.0
    assert sim.mae_r == (97.0 - 100.0) / 5.0        # -0.6, from the trigger bar
    assert sim.mfe_r == (111.0 - 100.0) / 5.0       # 2.2
    assert sim.mae_pct == -3.0
    assert sim.mfe_pct == 11.0


def test_time_stop_exit_and_no_censoring_when_window_fits():
    bars = _flat(3) + [(100, 101, 99, 100.5)] + [(102, 106, 99, 103)] * 30
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.exit_reason == "time"
    assert not sim.censored
    assert sim.bars_held == 20
    assert sim.exit_price == 103.0


def test_truncated_time_stop_is_censored():
    # Only 5 bars of data after the trigger — the 20-bar window ran off the end.
    bars = _flat(3) + [(100, 101, 99, 100.5)] + [(102, 106, 99, 103)] * 5
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert sim.exit_reason == "time"
    assert sim.censored


def test_untriggered_and_untriggered_censoring():
    # Never reaches 100 with a full window → settled non-trigger.
    bars = _flat(3) + _flat(15)
    sim = simulate_candidate(*_arrays(bars), 2, PIVOT, ATR, **PLAN)
    assert not sim.triggered and not sim.censored
    # Window cut short by end of data → not a settled outcome.
    bars_short = _flat(3) + _flat(4)
    sim2 = simulate_candidate(*_arrays(bars_short), 2, PIVOT, ATR, **PLAN)
    assert not sim2.triggered and sim2.censored


# ─── build_labels (DB): realignment, cooldown, forward returns ───────────────


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


def _candidate(scan_id, symbol: str, day_idx: int, bar_index: int, features=None):
    return BacktestSignalCandidate(
        scan_id=scan_id,
        symbol=symbol,
        signal_date=datetime(2025, 1, 6) + timedelta(days=day_idx),
        bar_index=bar_index,
        tt_score=6,
        vcp_score=Decimal("0.700"),
        pattern_type="vcp",
        pattern_quality=Decimal("0.700"),
        buyability="at_pivot",
        pivot_price=Decimal(str(PIVOT)),
        atr_at_signal=Decimal(str(ATR)),
        features=features,
    )


async def test_build_labels_realigns_cooldown_and_fwd_returns(db_session):
    signal_scan_service._bars_cache.clear()
    sym = "LBL"
    # Bars 0-10 flat 90; bar 11 triggers (entry 100); bar 13 hits the 110
    # target; bars 14+ flat at close 111.
    bars = []
    for i in range(11):
        bars.append(_bar(sym, i, 90, 91, 89, 90))
    bars.append(_bar(sym, 11, 100, 101, 99, 100.5))
    bars.append(_bar(sym, 12, 102, 105, 101, 104))
    bars.append(_bar(sym, 13, 105, 111, 104, 110.5))
    for i in range(14, 45):
        bars.append(_bar(sym, i, 110, 112, 109, 111))
    db_session.add_all(bars)

    scan = BacktestSignalScan(
        lookback_days=504, symbols_scanned=1, candidate_count=3,
        status="success", finished_at=datetime(2025, 3, 1),
    )
    db_session.add(scan)
    await db_session.flush()

    feats = {"fv": 1, "pattern_quality": 0.7}
    # Candidate A: signal day 10 but a deliberately STALE bar_index (3) —
    # must be realigned by date to bar 10.
    db_session.add(_candidate(scan.id, sym, day_idx=10, bar_index=3, features=feats))
    # Candidate B: day 12 — inside A's open trade (exit bar 13) → cooldown skip.
    db_session.add(_candidate(scan.id, sym, day_idx=12, bar_index=12))
    # Candidate C: a date that isn't in the bars at all → skipped (realign miss).
    db_session.add(_candidate(scan.id, sym, day_idx=200, bar_index=40))
    await db_session.commit()

    labels = await build_labels(db_session, scan_id=scan.id, stop_atr=1.0, target_r=2.0)

    assert len(labels) == 1
    lbl = labels[0]
    assert lbl.signal_date == "2025-01-16"           # day 10
    assert lbl.triggered and lbl.exit_reason == "target"
    assert lbl.days_to_trigger == 1
    assert lbl.r_multiple == 2.0
    assert lbl.features == feats
    assert lbl.mae_r == (99.0 - 100.0) / 5.0         # trigger-bar low 99
    assert lbl.mfe_r == (111.0 - 100.0) / 5.0
    # Forward returns from the entry bar (11): closes at bars 16/21/31 = 111.
    assert lbl.fwd_ret_5 == 11.0
    assert lbl.fwd_ret_10 == 11.0
    assert lbl.fwd_ret_20 == 11.0
