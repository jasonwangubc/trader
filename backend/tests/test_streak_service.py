"""Anti-martingale streak multiplier curve + outcome transitions."""
from __future__ import annotations

from decimal import Decimal

from app.db.models import TradeOutcome
from app.services.streak_service import _multiplier_for, get_snapshot, record_outcome


def test_multiplier_curve_boundaries():
    assert _multiplier_for(0, 0) == (Decimal("1.00"), False)
    assert _multiplier_for(1, 0) == (Decimal("1.00"), False)
    assert _multiplier_for(2, 0) == (Decimal("1.00"), False)
    assert _multiplier_for(3, 0) == (Decimal("1.50"), False)
    assert _multiplier_for(4, 0) == (Decimal("1.50"), False)
    assert _multiplier_for(5, 0) == (Decimal("2.00"), False)
    assert _multiplier_for(9, 0) == (Decimal("2.00"), False)
    assert _multiplier_for(0, 1) == (Decimal("1.00"), False)
    assert _multiplier_for(0, 2) == (Decimal("0.60"), False)
    assert _multiplier_for(0, 3) == (Decimal("0.30"), True)   # cooldown kicks in
    assert _multiplier_for(0, 7) == (Decimal("0.30"), True)


async def test_record_outcome_transitions(db_session):
    user = "streak_user"

    # Two losses -> 0.60x
    await record_outcome(db_session, outcome=TradeOutcome.LOSS, ticket_id=None, user_id=user)
    snap = await record_outcome(db_session, outcome=TradeOutcome.LOSS, ticket_id=None, user_id=user)
    assert snap.consecutive_losses == 2
    assert snap.multiplier == Decimal("0.60")

    # Scratch doesn't move the streak either way.
    snap = await record_outcome(db_session, outcome=TradeOutcome.SCRATCH, ticket_id=None, user_id=user)
    assert snap.consecutive_losses == 2
    assert snap.multiplier == Decimal("0.60")

    # A win resets losses.
    snap = await record_outcome(db_session, outcome=TradeOutcome.WIN, ticket_id=None, user_id=user)
    assert snap.consecutive_losses == 0
    assert snap.consecutive_wins == 1
    assert snap.multiplier == Decimal("1.00")


async def test_streaks_are_per_user(db_session):
    for _ in range(3):
        await record_outcome(
            db_session, outcome=TradeOutcome.LOSS, ticket_id=None, user_id="loser"
        )
    loser = await get_snapshot(db_session, user_id="loser")
    other = await get_snapshot(db_session, user_id="someone_else")
    assert loser.multiplier == Decimal("0.30")
    assert loser.cooldown_active
    assert other.multiplier == Decimal("1.00")
    assert not other.cooldown_active
