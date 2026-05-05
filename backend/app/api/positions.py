from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.registry import get_broker
from app.db.session import get_session
from app.services.positions_service import (
    buying_power_breakdown,
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


class BuyingPowerOut(BaseModel):
    currency: str
    cash: Decimal
    cash_equivalents: Decimal
    freeable_total: Decimal


class PositionsOut(BaseModel):
    positions: list[PositionOut]
    buying_power: list[BuyingPowerOut]


def _to_out(p) -> PositionOut:
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
    )


async def _buying_power(session: AsyncSession) -> list[BuyingPowerOut]:
    out: list[BuyingPowerOut] = []
    for currency in ("CAD", "USD"):
        bp = await buying_power_breakdown(session, currency=currency)
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
async def list_all(session: AsyncSession = Depends(get_session)) -> PositionsOut:
    positions = await list_positions(session)
    buying_power = await _buying_power(session)
    return PositionsOut(
        positions=[_to_out(p) for p in positions],
        buying_power=buying_power,
    )


@router.post("/sync", response_model=PositionsOut)
async def sync(session: AsyncSession = Depends(get_session)) -> PositionsOut:
    broker = get_broker()
    qt_broker = getattr(broker, "_quote_source", broker)
    try:
        await sync_positions(session, qt_broker)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    positions = await list_positions(session)
    buying_power = await _buying_power(session)
    return PositionsOut(
        positions=[_to_out(p) for p in positions],
        buying_power=buying_power,
    )
