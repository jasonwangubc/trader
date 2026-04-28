from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.registry import get_broker
from app.db.models import Account, AccountBalance
from app.db.session import get_session
from app.services.accounts_service import get_household_equity, sync_accounts

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class BalanceOut(BaseModel):
    currency: str
    cash: Decimal
    market_value: Decimal
    total_equity: Decimal
    buying_power: Decimal

    model_config = {"from_attributes": True}


class AccountOut(BaseModel):
    id: str
    questrade_account_id: str
    type: str
    primary_currency: str
    nickname: str | None
    real_money_enabled: bool
    balances: list[BalanceOut]

    model_config = {"from_attributes": True}


class HouseholdOut(BaseModel):
    accounts: list[AccountOut]
    household_equity: dict[str, Decimal]


@router.get("/sync", response_model=HouseholdOut)
async def sync(session: AsyncSession = Depends(get_session)) -> HouseholdOut:
    """Pull latest accounts + balances from Questrade and return them."""
    broker = get_broker()
    try:
        # Paper mode wraps Questrade; list_accounts hits the live QT broker.
        qt_broker = getattr(broker, "_quote_source", broker)
        accounts = await sync_accounts(session, qt_broker)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    equity = await get_household_equity(session)
    return HouseholdOut(
        accounts=[_account_to_out(a) for a in accounts],
        household_equity=equity,
    )


@router.get("", response_model=HouseholdOut)
async def list_accounts(session: AsyncSession = Depends(get_session)) -> HouseholdOut:
    """Return cached accounts from DB (no broker call)."""
    result = await session.execute(
        select(Account).where(Account.is_active == True).order_by(Account.created_at)  # noqa: E712
    )
    accounts = result.scalars().all()
    for a in accounts:
        await session.refresh(a, ["balances"])

    equity = await get_household_equity(session)
    return HouseholdOut(
        accounts=[_account_to_out(a) for a in accounts],
        household_equity=equity,
    )


def _account_to_out(a: Account) -> AccountOut:
    return AccountOut(
        id=str(a.id),
        questrade_account_id=a.questrade_account_id,
        type=a.type,
        primary_currency=a.primary_currency,
        nickname=a.nickname,
        real_money_enabled=a.real_money_enabled,
        balances=[
            BalanceOut(
                currency=b.currency,
                cash=b.cash,
                market_value=b.market_value,
                total_equity=b.total_equity,
                buying_power=b.buying_power,
            )
            for b in a.balances
        ],
    )
