from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.db.models import (
    Currency,
    Fill,
    Order,
    SetupType,
    Ticket,
    TicketStatus,
    TriggerType,
)
from app.db.session import get_session
from app.api.auth import get_user_id
from app.services.guardrail_service import GuardrailViolation, check_all
from app.services.order_service import close_ticket
from app.services.regime_service import get_regime
from app.services.trailing_service import TrailingSuggestion, compute_trailing_suggestion
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


class BuyingPowerOut(BaseModel):
    currency: str
    cash: Decimal
    cash_equivalents: Decimal
    freeable_total: Decimal


class GuardrailWarningOut(BaseModel):
    code: str
    message: str


class TicketPreviewOut(BaseModel):
    sizing: SizingOut
    streak: StreakOut
    buying_power: BuyingPowerOut
    regime: str
    guardrail_warnings: list[GuardrailWarningOut] = []


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
    is_paper: bool | None = None
    override_regime: bool = False   # explicitly proceed despite bear regime
    override_streak: bool = False   # explicitly proceed despite loss-streak block


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
    triggered_at: datetime | None
    filled_at: datetime | None
    closed_at: datetime | None
    expires_at: datetime | None
    realized_pnl: Decimal | None
    r_multiple: Decimal | None
    outcome: str | None
    exit_plan: dict | None = None

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
            triggered_at=t.triggered_at,
            filled_at=t.filled_at,
            closed_at=t.closed_at,
            expires_at=t.expires_at,
            realized_pnl=t.realized_pnl,
            r_multiple=t.r_multiple,
            outcome=t.outcome,
            exit_plan=t.exit_plan,
        )


class FillOut(BaseModel):
    id: uuid.UUID
    quantity: int
    price: Decimal
    occurred_at: datetime


class OrderOut(BaseModel):
    id: uuid.UUID
    intent: str
    side: str
    order_type: str
    quantity: int
    limit_price: Decimal | None
    stop_price: Decimal | None
    status: str
    submitted_at: datetime | None
    filled_at: datetime | None
    questrade_order_id: str | None
    fills: list[FillOut]


class TrailingOut(BaseModel):
    open_r: float
    new_stop: Decimal | None
    action: str
    urgency: str
    milestone_label: str


class TicketDetailOut(TicketOut):
    orders: list[OrderOut] = []
    exit_plan: dict | None = None
    trailing: TrailingOut | None = None


# -------- Routes --------

@router.post("/preview", response_model=TicketPreviewOut)
async def preview(
    body: TicketPreviewIn,
    session: AsyncSession = Depends(get_session),
) -> TicketPreviewOut:
    try:
        sizing, streak, buying_power = await preview_ticket(
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
        buying_power=BuyingPowerOut(
            currency=body.currency,
            cash=buying_power["cash"],
            cash_equivalents=buying_power["cash_equivalents"],
            freeable_total=buying_power["freeable_total"],
        ),
        regime=(await get_regime(session)).regime,
        guardrail_warnings=[],  # guardrails run on actual create, not preview
    )


@router.post("", response_model=TicketOut, status_code=201)
async def create(
    body: TicketIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
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

    # Behavioral guardrails
    regime = await get_regime(session)
    try:
        guardrail_warnings = await check_all(
            session,
            regime=regime,
            override_regime=body.override_regime,
            override_streak=body.override_streak,
        )
    except GuardrailViolation as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)})

    try:
        ticket = await create_ticket(
            session,
            user_id=user_id,
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


async def _get_ticket_for_user(
    session: AsyncSession, ticket_id: uuid.UUID, user_id: str
) -> Ticket:
    """Load a ticket and verify it belongs to the current user. Raises 404/403."""
    t = await session.get(Ticket, ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if t.user_id != user_id:
        # Return 404 to avoid leaking that the ticket exists at all
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.get("", response_model=list[TicketOut])
async def list_tickets(
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[TicketOut]:
    stmt = (
        select(Ticket)
        .where(Ticket.user_id == user_id)
        .order_by(Ticket.created_at.desc())
        .limit(200)
    )
    if status:
        stmt = stmt.where(Ticket.status == status)
    result = await session.execute(stmt)
    return [TicketOut.from_orm_obj(t) for t in result.scalars().all()]


@router.get("/{ticket_id}", response_model=TicketDetailOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TicketDetailOut:
    t = await _get_ticket_for_user(session, ticket_id, user_id)
    if t is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    orders_result = await session.execute(
        select(Order).where(Order.ticket_id == ticket_id).order_by(Order.submitted_at)
    )
    orders = orders_result.scalars().all()

    order_outs = []
    for o in orders:
        fills_result = await session.execute(
            select(Fill).where(Fill.order_id == o.id).order_by(Fill.occurred_at)
        )
        fills = fills_result.scalars().all()
        order_outs.append(OrderOut(
            id=o.id,
            intent=o.intent,
            side=o.side,
            order_type=o.order_type,
            quantity=o.quantity,
            limit_price=o.limit_price,
            stop_price=o.stop_price,
            status=o.status,
            submitted_at=o.submitted_at,
            filled_at=o.filled_at,
            questrade_order_id=o.questrade_order_id,
            fills=[FillOut(id=f.id, quantity=f.quantity, price=f.price, occurred_at=f.occurred_at)
                   for f in fills],
        ))

    # Trailing stop suggestion for filled tickets
    trailing_out = None
    if t.status == TicketStatus.FILLED.value:
        # Get entry fill price
        entry_price = t.trigger_price  # fallback
        fill_result = await session.execute(
            select(Fill).join(Order).where(
                Order.ticket_id == ticket_id,
                Order.intent == "entry",
                Fill.order_id == Order.id,
            ).order_by(Fill.occurred_at).limit(1)
        )
        fill = fill_result.scalar_one_or_none()
        if fill:
            entry_price = fill.price

        # Get last daily close from daily_bars
        from app.db.models import DailyBar
        bar_result = await session.execute(
            select(DailyBar)
            .where(DailyBar.symbol == t.symbol)
            .order_by(DailyBar.bar_date.desc())
            .limit(1)
        )
        last_bar = bar_result.scalar_one_or_none()
        if last_bar and last_bar.close > 0:
            suggestion = compute_trailing_suggestion(
                entry_price=entry_price,
                stop_price=t.stop_price,
                current_price=last_bar.close,
                shares=t.position_size_shares,
            )
            if suggestion:
                trailing_out = TrailingOut(
                    open_r=suggestion.open_r,
                    new_stop=suggestion.new_stop,
                    action=suggestion.action,
                    urgency=suggestion.urgency,
                    milestone_label=suggestion.milestone_label,
                )

    base = TicketOut.from_orm_obj(t)
    return TicketDetailOut(**base.model_dump(), orders=order_outs, trailing=trailing_out)


@router.post("/{ticket_id}/cancel", response_model=TicketOut)
async def cancel(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TicketOut:
    await _get_ticket_for_user(session, ticket_id, user_id)  # ownership check
    try:
        ticket = await cancel_ticket(session, ticket_id)
    except TicketValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return TicketOut.from_orm_obj(ticket)


class PyramidIn(BaseModel):
    add_price: Decimal = Field(gt=0)       # price at which you're adding
    add_shares: int    = Field(gt=0)       # shares being added


@router.post("/{ticket_id}/pyramid", response_model=TicketOut)
async def add_pyramid_entry(
    ticket_id: uuid.UUID,
    body: PyramidIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TicketOut:
    """Record an add-on (pyramid) entry on a filled ticket.
    Updates position_size_shares and recomputes blended cost basis.
    The stop price stays immutable — the unified stop manages the whole position.
    """
    from app.services.audit_service import log_event

    ticket = await _get_ticket_for_user(session, ticket_id, user_id)
    if ticket.status != TicketStatus.FILLED.value:
        raise HTTPException(400, detail="Can only pyramid into a filled position.")

    # Blended cost: (old_shares × avg_entry + add_shares × add_price) / total
    old_shares = ticket.position_size_shares
    # Find existing entry fill for blended calc
    fill_r = await session.execute(
        select(Fill).join(Order).where(
            Order.ticket_id == ticket_id, Order.intent == "entry"
        ).order_by(Fill.occurred_at).limit(1)
    )
    existing_fill = fill_r.scalar_one_or_none()
    old_price = existing_fill.price if existing_fill else ticket.trigger_price

    blended_price = ((old_price * old_shares + body.add_price * body.add_shares)
                     / (old_shares + body.add_shares))

    new_total = old_shares + body.add_shares
    ticket.position_size_shares = new_total
    ticket.position_size_value  = (blended_price * new_total).quantize(Decimal("0.01"))

    await log_event(
        session,
        actor="user",
        event_type="pyramid_entry",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol":         ticket.symbol,
            "add_price":      str(body.add_price),
            "add_shares":     body.add_shares,
            "blended_price":  str(blended_price.quantize(Decimal("0.0001"))),
            "new_total":      new_total,
        },
    )
    await session.commit()
    await session.refresh(ticket)
    return TicketOut.from_orm_obj(ticket)


class ExitLeg(BaseModel):
    price: Decimal = Field(gt=0)
    shares: int = Field(gt=0)
    label: str = ""    # e.g. "T1 +1.5R"


class ExitPlanIn(BaseModel):
    targets: list[ExitLeg]   # ordered cheapest-first


class CloseIn(BaseModel):
    exit_price: Decimal = Field(gt=0)
    exit_reason: str = Field(default="manual")
    close_reason_tag: str | None = None   # plan_target_hit | plan_stop_hit | panic_exit | etc.
    close_notes: str | None = None        # free-text reflection


@router.put("/{ticket_id}/exit-plan", response_model=TicketOut)
async def set_exit_plan(
    ticket_id: uuid.UUID,
    body: ExitPlanIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TicketOut:
    """Set or replace the staged exit plan for a filled ticket."""
    ticket = await _get_ticket_for_user(session, ticket_id, user_id)
    if ticket.status not in (TicketStatus.FILLED.value, TicketStatus.TRIGGERED.value):
        raise HTTPException(
            status_code=400,
            detail=f"Exit plan only applies to filled/triggered tickets (status: {ticket.status}).",
        )
    ticket.exit_plan = {
        "targets": [
            {"price": str(leg.price), "shares": leg.shares, "label": leg.label, "hit": False}
            for leg in body.targets
        ]
    }
    await session.commit()
    await session.refresh(ticket)
    return TicketOut.from_orm_obj(ticket)


@router.post("/{ticket_id}/close", response_model=TicketOut)
async def close(
    ticket_id: uuid.UUID,
    body: CloseIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TicketOut:
    """Manually record an exit for a filled ticket. Updates streak immediately."""
    ticket = await _get_ticket_for_user(session, ticket_id, user_id)
    if ticket.status != TicketStatus.FILLED.value:
        raise HTTPException(
            status_code=400,
            detail=f"Can only close a filled ticket (status is '{ticket.status}').",
        )
    await close_ticket(session, ticket, body.exit_price, body.exit_reason)
    # Persist qualitative journal fields
    if body.close_reason_tag:
        ticket.close_reason_tag = body.close_reason_tag
    if body.close_notes:
        ticket.close_notes = body.close_notes.strip() or None
    await session.commit()
    await session.refresh(ticket)
    return TicketOut.from_orm_obj(ticket)
