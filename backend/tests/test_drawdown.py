"""Account-level drawdown circuit breaker: tiers, override, sizing, scoping."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.db.models import EquitySnapshot
from app.services.accounts_service import (
    capture_equity_snapshots,
    get_peak_and_current_equity,
    set_active_account_id,
)
from app.services.guardrail_service import (
    GuardrailViolation,
    check_all,
    get_drawdown_state,
)
from app.services.settings_service import set_setting_json
from app.services.tickets_service import preview_ticket
from tests.factories import make_account

USER = "dd_user"


async def _seed_peak(session, account, *, equity: str, days_ago: int = 30, currency: str = "CAD"):
    day = datetime.utcnow() - timedelta(days=days_ago)
    session.add(EquitySnapshot(
        user_id=account.user_id,
        account_id=account.id,
        currency=currency,
        snapshot_date=datetime(day.year, day.month, day.day),
        total_equity=Decimal(equity),
        market_value=Decimal(equity),
        source="manual_seed",
    ))
    await session.flush()


async def test_no_history_means_no_breaker(db_session):
    await make_account(db_session, user_id=USER, equity=Decimal("50000"))
    # A same-or-lower peak means current IS the peak -> ok tier.
    state = await get_drawdown_state(db_session, USER)
    assert state.tier == "ok"
    assert state.risk_multiplier == Decimal("1.00")


async def test_tiers_at_thresholds(db_session):
    account = await make_account(db_session, user_id=USER, equity=Decimal("50000"))

    # Hand-computed cases against current = 50,000. Each seed uses a distinct
    # date; the peak is the max across history, so cases must be ascending.
    cases = [
        ("54000", "ok", 40),          # -7.4%
        ("56000", "warn", 41),        # -10.7%
        ("58000", "half_risk", 42),   # -13.8%
        ("60000", "block", 43),       # -16.7%
    ]
    for peak, expected, days_ago in cases:
        await _seed_peak(db_session, account, equity=peak, days_ago=days_ago)
        state = await get_drawdown_state(db_session, USER)
        assert state.tier == expected, f"peak={peak}: got {state.tier}"
        if expected in ("half_risk", "block"):
            assert state.risk_multiplier == Decimal("0.50")


async def test_block_raises_and_typed_override_warns(db_session):
    account = await make_account(db_session, user_id=USER, equity=Decimal("50000"))
    await _seed_peak(db_session, account, equity="60000")  # -16.7%

    with pytest.raises(GuardrailViolation) as exc:
        await check_all(db_session, user_id=USER)
    assert exc.value.code == "drawdown_block"

    warnings = await check_all(db_session, user_id=USER, override_drawdown=True)
    assert any(w.code == "drawdown_overridden" for w in warnings)


async def test_thresholds_configurable_per_user(db_session):
    account = await make_account(db_session, user_id=USER, equity=Decimal("50000"))
    await _seed_peak(db_session, account, equity="56000")  # -10.7%

    # Tighten the block threshold to 10% -> now blocked.
    await set_setting_json(
        db_session, f"{USER}:guardrail_config",
        {"dd_warn": 0.05, "dd_half_risk": 0.08, "dd_block": 0.10},
    )
    with pytest.raises(GuardrailViolation) as exc:
        await check_all(db_session, user_id=USER)
    assert exc.value.code == "drawdown_block"


async def test_sizing_halved_in_half_risk_tier(db_session):
    account = await make_account(db_session, user_id=USER, equity=Decimal("50000"))
    await _seed_peak(db_session, account, equity="58000")  # -13.8% -> half risk

    sizing, _, _ = await preview_ticket(
        db_session,
        account_id=account.id,
        currency="CAD",
        trigger_price=Decimal("100"),
        stop_price=Decimal("95"),
        user_id=USER,
    )
    # Default base risk 0.75% x 0.5 = 0.375% of 50k = $187.50 -> 37 shares @ $5 risk.
    assert sizing.shares == 37
    assert any("Drawdown breaker" in w for w in sizing.warnings)


async def test_drawdown_scoped_to_active_account(db_session):
    """A deep drawdown in a non-active account must not trip the breaker."""
    rrsp = await make_account(db_session, user_id=USER, account_type="RRSP", equity=Decimal("50000"))
    resp = await make_account(db_session, user_id=USER, account_type="RESP", equity=Decimal("10000"))
    await _seed_peak(db_session, resp, equity="20000")  # RESP down 50%

    await set_active_account_id(db_session, USER, rrsp.id)
    state = await get_drawdown_state(db_session, USER)
    assert state.tier == "ok"

    await set_active_account_id(db_session, USER, resp.id)
    state = await get_drawdown_state(db_session, USER)
    assert state.tier == "block"


async def test_capture_snapshots_upserts_per_day(db_session):
    from sqlalchemy import select

    account = await make_account(db_session, user_id=USER, equity=Decimal("50000"))
    n1 = await capture_equity_snapshots(db_session, USER, source="sync")
    n2 = await capture_equity_snapshots(db_session, USER, source="nightly")
    assert n1 == n2 == 1

    rows = (
        await db_session.execute(
            select(EquitySnapshot).where(EquitySnapshot.account_id == account.id)
        )
    ).scalars().all()
    assert len(rows) == 1               # same-day rows collapse
    assert rows[0].source == "nightly"  # last write wins

    peaks = await get_peak_and_current_equity(db_session, USER)
    assert peaks["CAD"] == (Decimal("50000.000000"), Decimal("50000.000000"))
