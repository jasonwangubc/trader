"""Pre-trade ticket creation. The discipline gate before any breakout monitor
can arm or any order can fire.

A ticket captures *every* decision at the unemotional moment of setup
identification: setup type, trigger, stop, target, time stop, sizing. Once
armed, sizing and stop are immutable — that's the whole point of the form.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Account, Ticket, TicketStatus
from app.services.accounts_service import get_household_equity
from app.services.audit_service import log_event
from app.services.sizing_service import SizingResult, compute_sizing
from app.services.streak_service import StreakSnapshot, get_snapshot


class TicketValidationError(Exception):
    """Raised when a ticket can't be created due to invalid inputs."""


async def preview_ticket(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    currency: str,
    trigger_price: Decimal,
    stop_price: Decimal,
) -> tuple[SizingResult, StreakSnapshot]:
    """Compute sizing + streak snapshot without persisting anything."""
    account = await session.get(Account, account_id)
    if account is None:
        raise TicketValidationError(f"Account {account_id} not found.")

    streak = await get_snapshot(session)
    equity = await get_household_equity(session)
    sizing = compute_sizing(
        trigger_price=trigger_price,
        stop_price=stop_price,
        currency=currency,
        equity_by_currency=equity,
        multiplier=streak.multiplier,
    )
    return sizing, streak


async def create_ticket(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    symbol: str,
    currency: str,
    setup_type: str,
    trigger_type: str,
    trigger_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal | None,
    time_stop_days: int | None,
    valid_for_days: int,
    volume_confirm_multiple: float | None,
    thesis: str,
    is_paper: bool | None = None,
) -> Ticket:
    """Create + arm a ticket. Snapshots sizing and streak state at creation."""
    account = await session.get(Account, account_id)
    if account is None:
        raise TicketValidationError(f"Account {account_id} not found.")

    # Resolve paper mode: explicit param wins, else account-level setting,
    # else global default.
    settings = get_settings()
    if is_paper is None:
        is_paper = not account.real_money_enabled or settings.paper_mode_default

    sizing, streak = await preview_ticket(
        session,
        account_id=account_id,
        currency=currency,
        trigger_price=trigger_price,
        stop_price=stop_price,
    )

    if sizing.shares <= 0:
        raise TicketValidationError(
            "Sizing returned 0 shares. " + " ".join(sizing.warnings)
        )

    now = datetime.now(timezone.utc)
    ticket = Ticket(
        account_id=account_id,
        symbol=symbol.upper().strip(),
        currency=currency,
        setup_type=setup_type,
        trigger_type=trigger_type,
        trigger_price=trigger_price,
        volume_confirm_multiple=volume_confirm_multiple,
        stop_price=stop_price,
        target_price=target_price,
        time_stop_days=time_stop_days,
        risk_pct=sizing.risk_pct,
        risk_amount=sizing.risk_amount,
        household_equity_at_creation=sizing.equity_basis,
        streak_multiplier_at_creation=sizing.multiplier,
        position_size_shares=sizing.shares,
        position_size_value=sizing.position_value,
        status=TicketStatus.ARMED.value,
        is_paper=is_paper,
        expires_at=now + timedelta(days=valid_for_days),
        armed_at=now,
        thesis=thesis.strip() or None,
    )
    session.add(ticket)
    await session.flush()  # populate ticket.id

    await log_event(
        session,
        actor="user",
        event_type="ticket_armed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol": ticket.symbol,
            "setup_type": setup_type,
            "trigger_type": trigger_type,
            "trigger_price": str(trigger_price),
            "stop_price": str(stop_price),
            "target_price": str(target_price) if target_price else None,
            "shares": sizing.shares,
            "risk_pct": str(sizing.risk_pct),
            "risk_amount": str(sizing.risk_amount),
            "streak_multiplier": str(sizing.multiplier),
            "is_paper": is_paper,
            "expires_at": ticket.expires_at.isoformat() if ticket.expires_at else None,
        },
    )

    await session.commit()
    await session.refresh(ticket)
    return ticket


async def cancel_ticket(session: AsyncSession, ticket_id: uuid.UUID) -> Ticket:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise TicketValidationError(f"Ticket {ticket_id} not found.")
    if ticket.status not in (TicketStatus.DRAFT.value, TicketStatus.ARMED.value):
        raise TicketValidationError(
            f"Cannot cancel ticket in status '{ticket.status}'."
        )
    ticket.status = TicketStatus.CANCELLED.value
    ticket.closed_at = datetime.now(timezone.utc)

    await log_event(
        session,
        actor="user",
        event_type="ticket_cancelled",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={"symbol": ticket.symbol},
    )

    await session.commit()
    await session.refresh(ticket)
    return ticket
