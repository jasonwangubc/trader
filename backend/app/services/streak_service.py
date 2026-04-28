"""Streak state singleton + risk-multiplier curve.

Streak-scaled risk is anti-martingale: size up on win streaks, down on loss
streaks. The curve here is the one we agreed on; tunable later via settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StreakState, TradeOutcome


@dataclass(frozen=True)
class StreakSnapshot:
    consecutive_wins: int
    consecutive_losses: int
    multiplier: Decimal
    cooldown_active: bool
    last_outcome: str | None


def _multiplier_for(wins: int, losses: int) -> tuple[Decimal, bool]:
    """Return (multiplier, cooldown_active) for the given streak counts."""
    if losses >= 3:
        return Decimal("0.30"), True
    if losses == 2:
        return Decimal("0.60"), False
    if wins >= 5:
        return Decimal("2.00"), False
    if wins >= 3:
        return Decimal("1.50"), False
    return Decimal("1.00"), False


async def get_or_create_streak(session: AsyncSession) -> StreakState:
    """Return the singleton streak row; create with defaults if missing."""
    row = await session.get(StreakState, 1)
    if row is None:
        row = StreakState(
            id=1,
            consecutive_wins=0,
            consecutive_losses=0,
            current_multiplier=Decimal("1.00"),
        )
        session.add(row)
        await session.flush()
    return row


async def get_snapshot(session: AsyncSession) -> StreakSnapshot:
    row = await get_or_create_streak(session)
    mult, cooldown = _multiplier_for(row.consecutive_wins, row.consecutive_losses)
    # Lazily heal the persisted multiplier if it drifted.
    if row.current_multiplier != mult:
        row.current_multiplier = mult
    return StreakSnapshot(
        consecutive_wins=row.consecutive_wins,
        consecutive_losses=row.consecutive_losses,
        multiplier=mult,
        cooldown_active=cooldown,
        last_outcome=row.last_outcome,
    )


async def record_outcome(
    session: AsyncSession,
    *,
    outcome: TradeOutcome,
    ticket_id,
) -> StreakSnapshot:
    """Update streak after a trade closes. Called from Sprint 2 fill/exit logic."""
    row = await get_or_create_streak(session)
    if outcome == TradeOutcome.WIN:
        row.consecutive_wins += 1
        row.consecutive_losses = 0
    elif outcome == TradeOutcome.LOSS:
        row.consecutive_losses += 1
        row.consecutive_wins = 0
    # Scratches don't move the streak in either direction.

    row.last_outcome = outcome.value
    row.last_ticket_id = ticket_id
    mult, _ = _multiplier_for(row.consecutive_wins, row.consecutive_losses)
    row.current_multiplier = mult
    await session.flush()
    return await get_snapshot(session)
