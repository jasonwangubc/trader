"""Behavioral guardrails — enforced at ticket-creation time.

Rules:
  1. Regime gate          — block new tickets when market is in bear regime
                            (configurable: "warn" | "block" | "off")
  2. Max concurrent arms  — cap on simultaneously armed tickets (default 5)
  3. Loss-streak block    — after N consecutive losses, require explicit
                            override flag (default 3)
  4. Revenge-trade cooldown — block new tickets within X hours of a
                              stopped_out close (default 24h)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket, TicketStatus
from app.services.regime_service import RegimeResult
from app.services.streak_service import get_snapshot

log = logging.getLogger(__name__)

# Defaults — override via settings table or environment later.
MAX_CONCURRENT_ARMED   = 8
LOSS_STREAK_BLOCK_AT   = 3       # block at this many consecutive losses
REVENGE_COOLDOWN_HOURS = 24      # hours after a stop-out before new ticket
REGIME_GATE_MODE       = "warn"  # "off" | "warn" | "block"


class GuardrailViolation(Exception):
    """Raised when a guardrail blocks ticket creation."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GuardrailWarning:
    """Soft warning — ticket can still be created but user is informed."""
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


async def check_all(
    session: AsyncSession,
    *,
    regime: RegimeResult | None = None,
    override_regime: bool = False,
    override_streak: bool = False,
) -> list[GuardrailWarning]:
    """Run all guardrails. Raises GuardrailViolation on hard blocks.
    Returns a list of soft warnings (informational only).
    """
    warnings: list[GuardrailWarning] = []

    # 1. Regime gate
    if regime and regime.regime != "bull":
        if REGIME_GATE_MODE == "block" and not override_regime:
            raise GuardrailViolation(
                "regime_bear",
                f"Market regime is '{regime.regime}' — new tickets are blocked. "
                "Wait for the broad market to reclaim its 200-day SMA, or pass override_regime=true.",
            )
        elif REGIME_GATE_MODE == "warn" or (REGIME_GATE_MODE == "block" and override_regime):
            warnings.append(GuardrailWarning(
                "regime_caution",
                f"Market regime is '{regime.regime}': {regime.message}",
            ))

    # 2. Max concurrent armed tickets
    armed_count_result = await session.execute(
        select(Ticket).where(Ticket.status == TicketStatus.ARMED.value)
    )
    armed = armed_count_result.scalars().all()
    if len(armed) >= MAX_CONCURRENT_ARMED:
        raise GuardrailViolation(
            "max_concurrent",
            f"You already have {len(armed)} armed tickets (limit: {MAX_CONCURRENT_ARMED}). "
            "Cancel or wait for existing tickets to trigger before adding more.",
        )

    # 3. Loss-streak block
    streak = await get_snapshot(session)
    if streak.consecutive_losses >= LOSS_STREAK_BLOCK_AT and not override_streak:
        raise GuardrailViolation(
            "loss_streak",
            f"{streak.consecutive_losses} consecutive losses. "
            "Review your recent trades before adding new risk. "
            "Pass override_streak=true to proceed anyway.",
        )
    elif streak.consecutive_losses >= LOSS_STREAK_BLOCK_AT - 1 and streak.consecutive_losses > 0:
        warnings.append(GuardrailWarning(
            "loss_streak_warning",
            f"{streak.consecutive_losses} consecutive loss(es) — cooldown approaching.",
        ))

    # 4. Revenge-trade cooldown
    cutoff = datetime.now(timezone.utc) - timedelta(hours=REVENGE_COOLDOWN_HOURS)
    recent_stopout = await session.execute(
        select(Ticket)
        .where(
            Ticket.status == TicketStatus.STOPPED_OUT.value,
            Ticket.closed_at >= cutoff,
        )
        .order_by(Ticket.closed_at.desc())
        .limit(1)
    )
    recent = recent_stopout.scalar_one_or_none()
    if recent:
        hrs_ago = (datetime.now(timezone.utc) - recent.closed_at).total_seconds() / 3600
        raise GuardrailViolation(
            "revenge_trade",
            f"You were stopped out of {recent.symbol} {hrs_ago:.1f}h ago. "
            f"Wait {REVENGE_COOLDOWN_HOURS - hrs_ago:.1f} more hours before adding new risk. "
            "This prevents revenge trading.",
        )

    return warnings
