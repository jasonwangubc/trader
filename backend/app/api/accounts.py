from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.registry import get_broker
from app.config import get_settings
from app.db.models import Account, AccountBalance, Ticket, TicketStatus
from app.db.session import get_session
from app.api.auth import get_user_id
from app.services.accounts_service import (
    get_active_account_id,
    get_household_equity,
    set_active_account_id,
    sync_accounts,
)
from app.services.audit_service import log_event
from app.services.positions_service import sync_positions

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
    # The single account the app is scoped to (sizing, risk, positions,
    # journal). None = all accounts (household view).
    active_account_id: str | None = None


@router.get("/sync", response_model=HouseholdOut)
async def sync(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> HouseholdOut:
    """Pull latest accounts, balances, and positions from Questrade."""
    broker = get_broker(user_id=user_id, session=session)
    try:
        qt_broker = getattr(broker, "_quote_source", broker)
        accounts = await sync_accounts(session, qt_broker, user_id=user_id)
        await sync_positions(session, qt_broker, user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # The accounts page always shows the true household total; scoping is
    # surfaced via active_account_id, not by hiding balances here.
    equity = await get_household_equity(session, user_id=user_id, scope_to_active=False)
    active_id = await get_active_account_id(session, user_id)
    return HouseholdOut(
        accounts=[_account_to_out(a) for a in accounts],
        household_equity=equity,
        active_account_id=str(active_id) if active_id else None,
    )


@router.get("", response_model=HouseholdOut)
async def list_accounts(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> HouseholdOut:
    """Return cached accounts from DB (no broker call)."""
    result = await session.execute(
        select(Account).where(
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        ).order_by(Account.created_at)
    )
    accounts = result.scalars().all()
    for a in accounts:
        await session.refresh(a, ["balances"])

    equity = await get_household_equity(session, user_id=user_id, scope_to_active=False)
    active_id = await get_active_account_id(session, user_id)
    return HouseholdOut(
        accounts=[_account_to_out(a) for a in accounts],
        household_equity=equity,
        active_account_id=str(active_id) if active_id else None,
    )


class ActiveAccountIn(BaseModel):
    account_id: uuid.UUID | None = None  # None clears the scope (all accounts)


@router.put("/active")
async def set_active_account(
    body: ActiveAccountIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> dict:
    """Scope the app to a single trading account (or clear with null)."""
    try:
        await set_active_account_id(session, user_id, body.account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await log_event(
        session,
        actor="user",
        event_type="active_account_changed",
        entity_type="account",
        entity_id=body.account_id,
        payload={"active_account_id": str(body.account_id) if body.account_id else None},
    )
    await session.commit()
    return {"active_account_id": str(body.account_id) if body.account_id else None}


class DrawdownStateOut(BaseModel):
    peak_equity: Decimal
    current_equity: Decimal
    currency: str
    drawdown_pct: float
    tier: str                    # ok | warn | half_risk | block
    risk_multiplier: Decimal
    has_history: bool
    dd_warn: float
    dd_half_risk: float
    dd_block: float


@router.get("/drawdown", response_model=DrawdownStateOut)
async def drawdown(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> DrawdownStateOut:
    """Current account drawdown vs peak (active-account scoped) + thresholds."""
    from app.services.guardrail_service import get_drawdown_state, load_guardrail_config

    config = await load_guardrail_config(session, user_id)
    state = await get_drawdown_state(session, user_id, config)
    return DrawdownStateOut(
        **state.__dict__,
        dd_warn=config.dd_warn,
        dd_half_risk=config.dd_half_risk,
        dd_block=config.dd_block,
    )


class EquitySeedIn(BaseModel):
    account_id: uuid.UUID
    currency: str
    snapshot_date: str           # "YYYY-MM-DD"
    total_equity: Decimal


@router.post("/equity-seed", status_code=201)
async def equity_seed(
    body: EquitySeedIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> dict:
    """Manually backfill a historical equity point (e.g. a pre-app peak).

    Equity history otherwise builds forward from the first sync — drawdowns
    relative to an earlier peak are invisible until seeded here.
    """
    from datetime import datetime as dt

    from app.db.models import EquitySnapshot

    account = await session.get(Account, body.account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")
    try:
        day = dt.strptime(body.snapshot_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="snapshot_date must be YYYY-MM-DD") from None

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(EquitySnapshot.__table__).values(
        user_id=user_id,
        account_id=account.id,
        currency=body.currency.upper(),
        snapshot_date=day,
        cash=Decimal(0),
        market_value=body.total_equity,
        total_equity=body.total_equity,
        source="manual_seed",
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_equity_snapshot_account_ccy_date",
        set_={"total_equity": stmt.excluded.total_equity,
              "market_value": stmt.excluded.market_value,
              "source": stmt.excluded.source},
    )
    await session.execute(stmt)
    await log_event(
        session,
        actor="user",
        event_type="equity_seed_added",
        entity_type="account",
        entity_id=account.id,
        payload={
            "currency": body.currency.upper(),
            "snapshot_date": body.snapshot_date,
            "total_equity": str(body.total_equity),
        },
    )
    await session.commit()
    return {"status": "ok"}


class AccountSettingsIn(BaseModel):
    real_money_enabled: bool
    nickname: str | None = None


@router.patch("/{account_id}", response_model=AccountOut)
async def update_account(
    account_id: uuid.UUID,
    body: AccountSettingsIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> AccountOut:
    """Toggle real-money execution and set nickname for an account."""
    account = await session.get(Account, account_id)
    if account is None or account.user_id != user_id:
        raise HTTPException(status_code=404, detail="Account not found")

    prev = account.real_money_enabled
    account.real_money_enabled = body.real_money_enabled
    if body.nickname is not None:
        account.nickname = body.nickname or None

    # Cascade: update is_paper on all armed tickets for this account so they
    # immediately reflect the new live/paper state without needing a re-save.
    if prev != body.real_money_enabled:
        settings = get_settings()
        new_is_paper = not body.real_money_enabled or settings.paper_mode_default
        armed_result = await session.execute(
            select(Ticket).where(
                Ticket.account_id == account.id,
                Ticket.status == TicketStatus.ARMED.value,
            )
        )
        for t in armed_result.scalars().all():
            t.is_paper = new_is_paper

    await log_event(
        session,
        actor="user",
        event_type="account_settings_changed",
        entity_type="account",
        entity_id=account.id,
        payload={
            "real_money_enabled": body.real_money_enabled,
            "previous": prev,
            "questrade_account_id": account.questrade_account_id,
        },
    )
    await session.commit()
    await session.refresh(account, ["balances"])
    return _account_to_out(account)


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
