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
from app.db.models import Account, Position, Ticket, TicketStatus, TriggerType, SetupType
from app.services.accounts_service import get_household_equity
from app.services.audit_service import log_event
from app.services.positions_service import buying_power_breakdown
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
) -> tuple[SizingResult, StreakSnapshot, dict[str, Decimal]]:
    """Compute sizing + streak snapshot + buying-power breakdown for the
    trade currency. Nothing is persisted."""
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
    buying_power = await buying_power_breakdown(session, currency=currency)
    return sizing, streak, buying_power


async def create_ticket(
    session: AsyncSession,
    *,
    user_id: str = "user_default",
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

    sizing, streak, _bp = await preview_ticket(
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
        user_id=user_id,
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


async def create_retroactive_ticket(
    session: AsyncSession,
    *,
    user_id: str,
    position_id: uuid.UUID,
    stop_price: Decimal,
    target_price: Decimal | None,
    setup_type: str,
    thesis: str,
) -> Ticket:
    """Create a FILLED ticket for an already-open broker position.

    Skips sizing, streak, regime, and guardrail logic — the position already
    exists, so those gates would be theatre. We snapshot risk for the journal
    but never block. The ticket immediately starts contributing to dashboard
    risk under the user-supplied stop.

    The Position is linked (`ticket_id` set, `is_managed=True`) so future
    syncs and exit detection treat it like any other ticketed trade.
    """
    pos = await session.get(Position, position_id)
    if pos is None:
        raise TicketValidationError("Position not found.")
    account = await session.get(Account, pos.account_id)
    if account is None or account.user_id != user_id:
        raise TicketValidationError("Position not found.")
    if pos.ticket_id is not None:
        raise TicketValidationError("This position is already linked to a ticket.")
    if pos.quantity <= 0:
        raise TicketValidationError("Cannot ticket a closed (zero-quantity) position.")
    if setup_type not in {s.value for s in SetupType}:
        raise TicketValidationError(f"Invalid setup_type: {setup_type}")

    # Use avg cost as the entry price snapshot. trigger_price is required by the
    # schema; for a filled retroactive ticket it's identical to entry price.
    entry_price = pos.avg_cost
    if entry_price <= 0:
        # Fallback for positions without a known cost basis (rare — fractional
        # shares, options assignment). Use current price as a sane default.
        entry_price = pos.current_price or stop_price * Decimal("1.05")
    if stop_price >= entry_price:
        raise TicketValidationError("Stop must be strictly below entry price for long retroactive tickets.")

    # Compute risk for journaling, but never block.
    per_share_risk = entry_price - stop_price
    risk_amount = per_share_risk * pos.quantity
    equity = await get_household_equity(session, user_id=user_id)
    equity_basis = equity.get(pos.currency, Decimal(0))
    risk_pct = (risk_amount / equity_basis) if equity_basis > 0 else Decimal(0)

    settings = get_settings()
    is_paper = not account.real_money_enabled or settings.paper_mode_default

    now = datetime.now(timezone.utc)
    ticket = Ticket(
        user_id=user_id,
        account_id=pos.account_id,
        symbol=pos.symbol,
        currency=pos.currency,
        setup_type=setup_type,
        trigger_type=TriggerType.PRICE_ABOVE.value,
        trigger_price=entry_price,
        volume_confirm_multiple=None,
        stop_price=stop_price,
        target_price=target_price,
        time_stop_days=None,
        risk_pct=risk_pct.quantize(Decimal("0.00001")),
        risk_amount=risk_amount.quantize(Decimal("0.01")),
        household_equity_at_creation=equity_basis,
        streak_multiplier_at_creation=Decimal("1.00"),
        position_size_shares=int(pos.quantity),
        position_size_value=(pos.quantity * entry_price).quantize(Decimal("0.01")),
        status=TicketStatus.FILLED.value,
        is_paper=is_paper,
        expires_at=None,
        armed_at=now,
        triggered_at=now,
        filled_at=now,
        thesis=thesis.strip() or None,
    )
    session.add(ticket)
    await session.flush()

    # Link the position so future syncs / exit detection treat it as managed.
    pos.ticket_id = ticket.id
    pos.is_managed = True

    await log_event(
        session,
        actor="user",
        event_type="ticket_retroactive_created",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol": ticket.symbol,
            "position_id": str(pos.id),
            "shares": int(pos.quantity),
            "entry_price": str(entry_price),
            "stop_price": str(stop_price),
            "target_price": str(target_price) if target_price else None,
            "risk_amount": str(risk_amount),
            "risk_pct": str(risk_pct),
            "setup_type": setup_type,
            "is_paper": is_paper,
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
