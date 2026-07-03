"""Behavioral guardrails: each rule blocks/warns correctly, per user."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.db.models import TicketStatus, TradeOutcome
from app.services.guardrail_service import (
    MAX_CONCURRENT_ARMED,
    GuardrailViolation,
    check_all,
)
from app.services.streak_service import record_outcome
from tests.factories import make_account, make_ticket

USER = "guardrail_user"


@dataclass
class FakeRegime:
    regime: str
    message: str = "test regime"


async def test_clean_slate_passes(db_session):
    warnings = await check_all(db_session, user_id=USER)
    assert warnings == []


async def test_regime_warn_mode_warns_not_blocks(db_session):
    warnings = await check_all(
        db_session, user_id=USER, regime=FakeRegime(regime="bear")
    )
    assert any(w.code == "regime_caution" for w in warnings)


async def test_max_armed_blocks_at_limit(db_session):
    account = await make_account(db_session, user_id=USER)
    for i in range(MAX_CONCURRENT_ARMED):
        await make_ticket(db_session, account, symbol=f"SYM{i}")
    with pytest.raises(GuardrailViolation) as exc:
        await check_all(db_session, user_id=USER)
    assert exc.value.code == "max_concurrent"


async def test_max_armed_one_below_limit_passes(db_session):
    account = await make_account(db_session, user_id=USER)
    for i in range(MAX_CONCURRENT_ARMED - 1):
        await make_ticket(db_session, account, symbol=f"SYM{i}")
    warnings = await check_all(db_session, user_id=USER)
    assert all(w.code != "max_concurrent" for w in warnings)


async def test_loss_streak_blocks_and_override_unblocks(db_session):
    for _ in range(3):
        await record_outcome(
            db_session, outcome=TradeOutcome.LOSS, ticket_id=None, user_id=USER
        )
    with pytest.raises(GuardrailViolation) as exc:
        await check_all(db_session, user_id=USER)
    assert exc.value.code == "loss_streak"

    warnings = await check_all(db_session, user_id=USER, override_streak=True)
    assert isinstance(warnings, list)  # override lets it through


async def test_two_losses_warns_only(db_session):
    for _ in range(2):
        await record_outcome(
            db_session, outcome=TradeOutcome.LOSS, ticket_id=None, user_id=USER
        )
    warnings = await check_all(db_session, user_id=USER)
    assert any(w.code == "loss_streak_warning" for w in warnings)


async def test_revenge_cooldown_inside_and_outside_window(db_session):
    account = await make_account(db_session, user_id=USER)
    # Stop-out 2h ago -> blocked
    await make_ticket(
        db_session, account, symbol="RVG",
        status=TicketStatus.STOPPED_OUT.value, closed_hours_ago=2,
    )
    with pytest.raises(GuardrailViolation) as exc:
        await check_all(db_session, user_id=USER)
    assert exc.value.code == "revenge_trade"


async def test_revenge_cooldown_expired_passes(db_session):
    account = await make_account(db_session, user_id=USER)
    await make_ticket(
        db_session, account, symbol="RVG",
        status=TicketStatus.STOPPED_OUT.value, closed_hours_ago=25,
    )
    warnings = await check_all(db_session, user_id=USER)
    assert isinstance(warnings, list)


async def test_guardrails_are_per_user(db_session):
    """Regression for the user-scoping bug: another user's armed tickets,
    loss streak, and stop-outs must not block this user."""
    other = await make_account(db_session, user_id="other_user")
    for i in range(MAX_CONCURRENT_ARMED):
        await make_ticket(db_session, other, symbol=f"OTH{i}")
    await make_ticket(
        db_session, other, symbol="OTHRVG",
        status=TicketStatus.STOPPED_OUT.value, closed_hours_ago=1,
    )
    for _ in range(3):
        await record_outcome(
            db_session, outcome=TradeOutcome.LOSS, ticket_id=None, user_id="other_user"
        )

    # This user is untouched by any of it.
    warnings = await check_all(db_session, user_id=USER)
    assert warnings == []
