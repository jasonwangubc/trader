from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Currency,
    SetupType,
    Ticket,
    TicketStatus,
    TriggerType,
)
from app.db.session import get_session
from app.services.tickets_service import (
    TicketValidationError,
    cancel_ticket,
    create_ticket,
    preview_ticket,
)

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


# -------- Schemas --------

class TicketPreviewIn(BaseModel):
    account_id: uuid.UUID
    currency: str
    trigger_price: Decimal
    stop_price: Decimal


class StreakOut(BaseModel):
    consecutive_wins: int
    consecutive_losses: int
    multiplier: Decimal
    cooldown_active: bool
    last_outcome: str | None


class SizingOut(BaseModel):
    risk_pct: Decimal
    base_risk_pct: Decimal
    multiplier: Decimal
    capped: bool
    equity_basis: Decimal
    equity_currency: str
    risk_amount: Decimal
    per_share_risk: Decimal
    shares: int
    position_value: Decimal
    warnings: list[str]


class TicketPreviewOut(BaseModel):
    sizing: SizingOut
    streak: StreakOut


class TicketIn(BaseModel):
    account_id: uuid.UUID
    symbol: str = Field(min_length=1, max_length=32)
    currency: str
    setup_type: str
    trigger_type: str
    trigger_price: Decimal
    volume_confirm_multiple: float | None = None
    stop_price: Decimal
    target_price: Decimal | None = None
    time_stop_days: int | None = Field(default=None, ge=1, le=365)
    valid_for_days: int = Field(default=7, ge=1, le=90)
    thesis: str = Field(min_length=10, max_length=2000)
    is_paper: bool | None = None  # None => resolved server-side


class TicketOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    symbol: str
    currency: str
    setup_type: str
    trigger_type: str
    trigger_price: Decimal
    stop_price: Decimal
    target_price: Decimal | None
    time_stop_days: int | None
    risk_pct: Decimal
    risk_amount: Decimal
    streak_multiplier_at_creation: Decimal
    position_size_shares: int
    position_size_value: Decimal
    status: str
    is_paper: bool
    thesis: str | None
    created_at: datetime
    armed_at: datetime | None
    expires_at: datetime | None

    @classmethod
    def from_orm_obj(cls, t: Ticket) -> "TicketOut":
        return cls(
            id=t.id,
            account_id=t.account_id,
            symbol=t.symbol,
            currency=t.currency,
            setup_type=t.setup_type,
            trigger_type=t.trigger_type,
            trigger_price=t.trigger_price,
            stop_price=t.stop_price,
            target_price=t.target_price,
            time_stop_days=t.time_stop_days,
            risk_pct=t.risk_pct,
            risk_amount=t.risk_amount,
            streak_multiplier_at_creation=t.streak_multiplier_at_creation,
            position_size_shares=t.position_size_shares,
            position_size_value=t.position_size_value,
            status=t.status,
            is_paper=t.is_paper,
            thesis=t.thesis,
            created_at=t.created_at,
            armed_at=t.armed_at,
            expires_at=t.expires_at,
        )


# -------- Routes --------

@router.post("/preview", response_model=TicketPreviewOut)
async def preview(
    body: TicketPreviewIn,
    session: AsyncSession = Depends(get_session),
) -> TicketPreviewOut:
    try:
        sizing, streak = await preview_ticket(
            session,
            account_id=body.account_id,
            currency=body.currency,
            trigger_price=body.trigger_price,
            stop_price=body.stop_price,
        )
    except TicketValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return TicketPreviewOut(
        sizing=SizingOut(
            risk_pct=sizing.risk_pct,
            base_risk_pct=sizing.base_risk_pct,
            multiplier=sizing.multiplier,
            capped=sizing.capped,
            equity_basis=sizing.equity_basis,
            equity_currency=sizing.equity_currency,
            risk_amount=sizing.risk_amount,
            per_share_risk=sizing.per_share_risk,
            shares=sizing.shares,
            position_value=sizing.position_value,
            warnings=sizing.warnings,
        ),
        streak=StreakOut(
            consecutive_wins=streak.consecutive_wins,
            consecutive_losses=streak.consecutive_losses,
            multiplier=streak.multiplier,
            cooldown_active=streak.cooldown_active,
            last_outcome=streak.last_outcome,
        ),
    )


@router.post("", response_model=TicketOut, status_code=201)
async def create(
    body: TicketIn,
    session: AsyncSession = Depends(get_session),
) -> TicketOut:
    # Validate enums
    if body.currency not in {c.value for c in Currency}:
        raise HTTPException(status_code=400, detail=f"Invalid currency: {body.currency}")
    if body.setup_type not in {s.value for s in SetupType}:
        raise HTTPException(status_code=400, detail=f"Invalid setup_type: {body.setup_type}")
    if body.trigger_type not in {t.value for t in TriggerType}:
        raise HTTPException(status_code=400, detail=f"Invalid trigger_type: {body.trigger_type}")
    if body.trigger_price <= body.stop_price:
        raise HTTPException(
            status_code=400,
            detail="Stop must be strictly below trigger for long entries.",
        )

    try:
        ticket = await create_ticket(
            session,
            account_id=body.account_id,
            symbol=body.symbol,
            currency=body.currency,
            setup_type=body.setup_type,
            trigger_type=body.trigger_type,
            trigger_price=body.trigger_price,
            stop_price=body.stop_price,
            target_price=body.target_price,
            time_stop_days=body.time_stop_days,
            valid_for_days=body.valid_for_days,
            volume_confirm_multiple=body.volume_confirm_multiple,
            thesis=body.thesis,
            is_paper=body.is_paper,
        )
    except TicketValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return TicketOut.from_orm_obj(ticket)


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[TicketOut]:
    stmt = select(Ticket).order_by(Ticket.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Ticket.status == status)
    result = await session.execute(stmt)
    return [TicketOut.from_orm_obj(t) for t in result.scalars().all()]


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> TicketOut:
    t = await session.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketOut.from_orm_obj(t)


@router.post("/{ticket_id}/cancel", response_model=TicketOut)
async def cancel(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> TicketOut:
    try:
        ticket = await cancel_ticket(session, ticket_id)
    except TicketValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TicketOut.from_orm_obj(ticket)
