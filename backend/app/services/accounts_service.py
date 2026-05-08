"""Sync Questrade accounts + balances into our DB, return a snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.db.models import Account, AccountBalance


async def sync_accounts(
    session: AsyncSession,
    broker: BrokerInterface,
    user_id: str = "user_default",
) -> list[Account]:
    """Pull accounts + balances from broker and upsert into DB."""
    from app.db.models import USER_DEFAULT
    broker_accounts = await broker.list_accounts()

    synced: list[Account] = []
    for ba in broker_accounts:
        # Upsert account row — scoped to this user
        result = await session.execute(
            select(Account).where(
                Account.questrade_account_id == ba.broker_account_id,
                Account.user_id == user_id,
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            account = Account(
                user_id=user_id,
                questrade_account_id=ba.broker_account_id,
                type=ba.type,
                primary_currency=ba.primary_currency,
                real_money_enabled=False,
            )
            session.add(account)
            await session.flush()  # get the generated id

        # Sync balances
        broker_balances = await broker.get_balances(ba.broker_account_id)
        for bb in broker_balances:
            result = await session.execute(
                select(AccountBalance).where(
                    AccountBalance.account_id == account.id,
                    AccountBalance.currency == bb.currency,
                )
            )
            bal = result.scalar_one_or_none()
            if bal is None:
                bal = AccountBalance(account_id=account.id, currency=bb.currency)
                session.add(bal)
            bal.cash = bb.cash
            bal.market_value = bb.market_value
            bal.total_equity = bb.total_equity
            bal.buying_power = bb.buying_power
            bal.maintenance_excess = bb.maintenance_excess
            bal.as_of = datetime.now(timezone.utc)

        synced.append(account)

    await session.commit()

    # Reload with balances
    for account in synced:
        await session.refresh(account, ["balances"])

    return synced


async def get_household_equity(
    session: AsyncSession,
    user_id: str = "user_default",
) -> dict[str, Decimal]:
    """Sum total_equity across all active accounts for a user, by currency."""
    result = await session.execute(
        select(AccountBalance).join(Account).where(
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        )
    )
    balances = result.scalars().all()
    totals: dict[str, Decimal] = {}
    for b in balances:
        totals[b.currency] = totals.get(b.currency, Decimal(0)) + b.total_equity
    return totals
