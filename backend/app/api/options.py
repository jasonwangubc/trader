"""Options income/hedge tickets — covered calls and cash-secured puts."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, OptionStatus, OptionStrategy, OptionTicket
from app.db.session import get_session
from app.services.audit_service import log_event

router = APIRouter(prefix="/api/options", tags=["options"])


class OptionTicketIn(BaseModel):
    account_id: uuid.UUID
    underlying_symbol: str = Field(min_length=1, max_length=32)
    currency: str
    strategy: str                              # covered_call | cash_secured_put
    strike_price: Decimal = Field(gt=0)
    expiry_date: date
    contracts: int = Field(default=1, ge=1)
    premium_received: Decimal = Field(gt=0)    # per share
    thesis: str | None = None
    is_paper: bool | None = None


class CloseOptionIn(BaseModel):
    premium_paid_to_close: Decimal | None = Field(default=None, ge=0)
    # None = expired worthless (premium_paid = 0)
    outcome: str = "closed"   # "closed" | "expired" | "assigned"


class OptionTicketOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    underlying_symbol: str
    currency: str
    strategy: str
    option_type: str
    strike_price: Decimal
    expiry_date: datetime
    contracts: int
    premium_received: Decimal
    total_premium: Decimal          # premium_received × 100 × contracts
    break_even: Decimal | None
    status: str
    is_paper: bool
    thesis: str | None
    premium_paid_to_close: Decimal | None
    realized_pnl: Decimal | None
    closed_at: datetime | None
    created_at: datetime


def _to_out(t: OptionTicket) -> OptionTicketOut:
    total = t.premium_received * 100 * t.contracts
    return OptionTicketOut(
        id=t.id,
        account_id=t.account_id,
        underlying_symbol=t.underlying_symbol,
        currency=t.currency,
        strategy=t.strategy,
        option_type=t.option_type,
        strike_price=t.strike_price,
        expiry_date=t.expiry_date,
        contracts=t.contracts,
        premium_received=t.premium_received,
        total_premium=total,
        break_even=t.break_even,
        status=t.status,
        is_paper=t.is_paper,
        thesis=t.thesis,
        premium_paid_to_close=t.premium_paid_to_close,
        realized_pnl=t.realized_pnl,
        closed_at=t.closed_at,
        created_at=t.created_at,
    )


@router.get("", response_model=list[OptionTicketOut])
async def list_options(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[OptionTicketOut]:
    q = select(OptionTicket).order_by(OptionTicket.created_at.desc())
    if status:
        q = q.where(OptionTicket.status == status)
    result = await session.execute(q)
    return [_to_out(t) for t in result.scalars().all()]


@router.post("", response_model=OptionTicketOut, status_code=201)
async def create_option(
    body: OptionTicketIn,
    session: AsyncSession = Depends(get_session),
) -> OptionTicketOut:
    if body.strategy not in {s.value for s in OptionStrategy}:
        raise HTTPException(400, f"Invalid strategy: {body.strategy}")

    account = await session.get(Account, body.account_id)
    if account is None:
        raise HTTPException(404, "Account not found")

    is_paper = body.is_paper if body.is_paper is not None else (not account.real_money_enabled)

    opt_type = "call" if body.strategy == OptionStrategy.COVERED_CALL.value else "put"

    # Break-even:
    # CC: underlying cost_basis - premium (not tracked here; use strike - premium)
    # CSP: strike - premium
    break_even = body.strike_price - body.premium_received

    t = OptionTicket(
        account_id=body.account_id,
        underlying_symbol=body.underlying_symbol.upper().strip(),
        currency=body.currency,
        strategy=body.strategy,
        option_type=opt_type,
        strike_price=body.strike_price,
        expiry_date=datetime.combine(body.expiry_date, datetime.min.time()),
        contracts=body.contracts,
        premium_received=body.premium_received,
        break_even=break_even,
        status=OptionStatus.OPEN.value,
        is_paper=is_paper,
        thesis=body.thesis,
    )
    session.add(t)
    await session.flush()

    await log_event(
        session,
        actor="user",
        event_type="option_ticket_created",
        entity_type="option_ticket",
        entity_id=t.id,
        payload={
            "symbol": t.underlying_symbol,
            "strategy": t.strategy,
            "strike": str(t.strike_price),
            "expiry": body.expiry_date.isoformat(),
            "contracts": t.contracts,
            "premium": str(t.premium_received),
            "total_premium": str(t.premium_received * 100 * t.contracts),
            "is_paper": is_paper,
        },
    )
    await session.commit()
    await session.refresh(t)
    return _to_out(t)


@router.post("/{ticket_id}/close", response_model=OptionTicketOut)
async def close_option(
    ticket_id: uuid.UUID,
    body: CloseOptionIn,
    session: AsyncSession = Depends(get_session),
) -> OptionTicketOut:
    t = await session.get(OptionTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "Option ticket not found")
    if t.status != OptionStatus.OPEN.value:
        raise HTTPException(400, f"Cannot close ticket in status '{t.status}'")

    paid = body.premium_paid_to_close or Decimal(0)
    total_received = t.premium_received * 100 * t.contracts
    total_paid     = paid * 100 * t.contracts
    t.premium_paid_to_close = paid
    t.realized_pnl = total_received - total_paid
    t.closed_at = datetime.now()
    t.status = body.outcome  # "closed" | "expired" | "assigned"

    await log_event(
        session,
        actor="user",
        event_type="option_ticket_closed",
        entity_type="option_ticket",
        entity_id=t.id,
        payload={
            "symbol": t.underlying_symbol,
            "outcome": body.outcome,
            "premium_paid_to_close": str(paid),
            "realized_pnl": str(t.realized_pnl),
        },
    )
    await session.commit()
    await session.refresh(t)
    return _to_out(t)
