"""Trailing stop coaching actions — list, confirm, dismiss."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_id
from app.brokers.registry import get_broker
from app.db.models import Account, Ticket, TicketStatus, TrailingAction
from app.db.session import get_session
from app.services.order_service import place_scale_out_order, replace_stop_order

router = APIRouter(prefix="/api/trailing", tags=["trailing"])


class TrailingActionOut(BaseModel):
    id: uuid.UUID
    ticket_id: uuid.UUID
    symbol: str
    action_type: str          # "trail_stop" | "scale_out"
    milestone: str
    old_stop: Decimal | None
    new_stop: Decimal | None
    sell_price: Decimal | None
    sell_shares: int | None
    leg_label: str | None
    open_r: Decimal
    triggered_price: Decimal
    triggered_at: datetime
    status: str
    confirmed_at: datetime | None
    executed_at: datetime | None
    execution_price: Decimal | None
    error_msg: str | None
    is_paper: bool


async def _load_action(
    session: AsyncSession, action_id: uuid.UUID, user_id: str
) -> tuple[TrailingAction, Ticket]:
    action = await session.get(TrailingAction, action_id)
    if action is None or action.user_id != user_id:
        raise HTTPException(status_code=404, detail="Action not found")
    ticket = await session.get(Ticket, action.ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return action, ticket


def _action_out(action: TrailingAction, symbol: str, is_paper: bool) -> TrailingActionOut:
    return TrailingActionOut(
        id=action.id,
        ticket_id=action.ticket_id,
        symbol=symbol,
        action_type=action.action_type,
        milestone=action.milestone,
        old_stop=action.old_stop,
        new_stop=action.new_stop,
        sell_price=action.sell_price,
        sell_shares=action.sell_shares,
        leg_label=action.leg_label,
        open_r=action.open_r,
        triggered_price=action.triggered_price,
        triggered_at=action.triggered_at,
        status=action.status,
        confirmed_at=action.confirmed_at,
        executed_at=action.executed_at,
        execution_price=action.execution_price,
        error_msg=action.error_msg,
        is_paper=is_paper,
    )


@router.get("/actions", response_model=list[TrailingActionOut])
async def list_actions(
    status: str = "pending",
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[TrailingActionOut]:
    """List trailing coaching actions. Default: pending only."""
    result = await session.execute(
        select(TrailingAction)
        .where(
            TrailingAction.user_id == user_id,
            TrailingAction.status == status,
        )
        .order_by(TrailingAction.triggered_at.desc())
    )
    actions = result.scalars().all()
    out = []
    for a in actions:
        ticket = await session.get(Ticket, a.ticket_id)
        if ticket:
            out.append(_action_out(a, ticket.symbol, ticket.is_paper))
    return out


@router.post("/actions/{action_id}/confirm", response_model=TrailingActionOut)
async def confirm_action(
    action_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TrailingActionOut:
    """Execute a pending trailing action: cancel/replace stop or place scale-out sell."""
    action, ticket = await _load_action(session, action_id, user_id)

    if action.status != "pending":
        raise HTTPException(status_code=400, detail=f"Action is already {action.status}")
    if ticket.status != TicketStatus.FILLED.value:
        raise HTTPException(status_code=400, detail="Ticket is no longer open")

    now = datetime.now(timezone.utc)
    action.confirmed_at = now
    action.status = "confirmed"

    broker = get_broker(user_id=user_id)
    # PaperBroker wraps real quote source — use it as-is for paper tickets
    try:
        if action.action_type == "trail_stop":
            if action.new_stop is None:
                raise ValueError("new_stop is required for trail_stop action")
            order = await replace_stop_order(session, ticket, action.new_stop, broker)
            action.broker_order_id = order.questrade_order_id
            action.execution_price = action.new_stop

        elif action.action_type == "scale_out":
            if action.sell_price is None or action.sell_shares is None:
                raise ValueError("sell_price and sell_shares required for scale_out")
            order = await place_scale_out_order(
                session, ticket, action.sell_price, action.sell_shares, broker
            )
            action.broker_order_id = order.questrade_order_id
            action.execution_price = action.sell_price

        else:
            raise HTTPException(status_code=400, detail=f"Unknown action_type: {action.action_type}")

        action.status = "executed"
        action.executed_at = datetime.now(timezone.utc)

    except Exception as exc:
        action.status = "failed"
        action.error_msg = str(exc)[:480]

    await session.commit()
    await session.refresh(action)
    return _action_out(action, ticket.symbol, ticket.is_paper)


@router.post("/actions/{action_id}/dismiss", response_model=TrailingActionOut)
async def dismiss_action(
    action_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TrailingActionOut:
    """Dismiss a pending action without executing it (you'll handle it manually)."""
    action, ticket = await _load_action(session, action_id, user_id)
    if action.status != "pending":
        raise HTTPException(status_code=400, detail=f"Action is already {action.status}")
    action.status = "dismissed"
    action.confirmed_at = datetime.now(timezone.utc)
    await session.commit()
    return _action_out(action, ticket.symbol, ticket.is_paper)
