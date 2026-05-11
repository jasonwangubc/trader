from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.registry import get_broker
from app.db.models import Position
from app.db.session import get_session
from app.api.auth import get_user_id
from app.services.positions_service import (
    BrokerStopTarget,
    buying_power_breakdown,
    fetch_broker_stop_targets,
    is_cash_equivalent,
    list_positions,
    sync_positions,
)

router = APIRouter(prefix="/api/positions", tags=["positions"])


class PositionOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    symbol: str
    currency: str
    quantity: Decimal
    avg_cost: Decimal
    current_price: Decimal | None
    market_value: Decimal
    open_pnl: Decimal
    is_cash_equivalent: bool
    is_managed: bool
    is_buy_and_hold: bool
    ticket_id: uuid.UUID | None
    as_of: datetime

    # Broker-side stop/target derived from open sell orders (best-effort).
    # Lets the UI tell "unmanaged but stopped" apart from "unmanaged, no stop."
    broker_stop_price: Decimal | None = None
    broker_target_price: Decimal | None = None
    broker_open_order_count: int = 0


class PositionPatch(BaseModel):
    is_buy_and_hold: bool | None = None


class BuyingPowerOut(BaseModel):
    currency: str
    cash: Decimal
    cash_equivalents: Decimal
    freeable_total: Decimal


class PositionsOut(BaseModel):
    positions: list[PositionOut]
    buying_power: list[BuyingPowerOut]


def _to_out(p, st: BrokerStopTarget | None = None) -> PositionOut:
    return PositionOut(
        id=p.id,
        account_id=p.account_id,
        symbol=p.symbol,
        currency=p.currency,
        quantity=p.quantity,
        avg_cost=p.avg_cost,
        current_price=p.current_price,
        market_value=p.market_value,
        open_pnl=p.open_pnl,
        is_cash_equivalent=is_cash_equivalent(p.symbol),
        is_managed=p.is_managed,
        is_buy_and_hold=p.is_buy_and_hold,
        ticket_id=p.ticket_id,
        as_of=p.as_of,
        broker_stop_price=st.stop_price if st else None,
        broker_target_price=st.target_price if st else None,
        broker_open_order_count=st.open_order_count if st else 0,
    )


async def _annotate_with_broker_orders(
    session: AsyncSession,
    user_id: str,
    positions: list,
) -> list[PositionOut]:
    """Annotate each position with the broker-side stop/target when available.
    Best-effort — broker errors fall through to no annotations."""
    stop_targets: dict[tuple[uuid.UUID, str], BrokerStopTarget] = {}
    try:
        broker = get_broker(user_id=user_id)
        # PaperBroker wraps a real quote_source for reads; unwrap to query orders.
        order_broker = getattr(broker, "_quote_source", broker)
        stop_targets = await fetch_broker_stop_targets(
            user_id=user_id, session=session, broker=order_broker
        )
    except Exception:
        pass
    return [_to_out(p, stop_targets.get((p.account_id, p.symbol))) for p in positions]


async def _buying_power(session: AsyncSession, user_id: str = "user_default") -> list[BuyingPowerOut]:
    out: list[BuyingPowerOut] = []
    for currency in ("CAD", "USD"):
        bp = await buying_power_breakdown(session, currency=currency, user_id=user_id)
        out.append(
            BuyingPowerOut(
                currency=currency,
                cash=bp["cash"],
                cash_equivalents=bp["cash_equivalents"],
                freeable_total=bp["freeable_total"],
            )
        )
    return out


@router.get("", response_model=PositionsOut)
async def list_all(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> PositionsOut:
    positions = await list_positions(session, user_id=user_id)
    annotated = await _annotate_with_broker_orders(session, user_id, positions)
    buying_power = await _buying_power(session, user_id=user_id)
    return PositionsOut(positions=annotated, buying_power=buying_power)


@router.post("/sync", response_model=PositionsOut)
async def sync(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> PositionsOut:
    broker = get_broker(user_id=user_id)
    qt_broker = getattr(broker, "_quote_source", broker)
    try:
        await sync_positions(session, qt_broker, user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    positions = await list_positions(session, user_id=user_id)
    annotated = await _annotate_with_broker_orders(session, user_id, positions)
    buying_power = await _buying_power(session, user_id=user_id)
    return PositionsOut(positions=annotated, buying_power=buying_power)


@router.patch("/{position_id}", response_model=PositionOut)
async def update_position(
    position_id: uuid.UUID,
    body: PositionPatch,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> PositionOut:
    """Mutable user-controlled flags on a position. Right now: is_buy_and_hold
    only — the rest of the fields are owned by the broker sync."""
    from app.db.models import Account
    p = await session.get(Position, position_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Position not found")
    acct = await session.get(Account, p.account_id)
    if acct is None or acct.user_id != user_id:
        raise HTTPException(status_code=404, detail="Position not found")

    if body.is_buy_and_hold is not None:
        p.is_buy_and_hold = body.is_buy_and_hold
    await session.commit()
    await session.refresh(p)
    return _to_out(p)
