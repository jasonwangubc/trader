"""Sync Questrade accounts + balances into our DB, return a snapshot.

Also owns the per-user "active trading account" setting: when set, equity,
positions, and journal views scope to that single account so other accounts
(RESP, etc.) don't add noise or inflate the sizing basis.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.brokers.base import BrokerInterface
from app.db.models import Account, AccountBalance, EquitySnapshot
from app.services.settings_service import del_setting, get_setting, set_setting


def _active_account_key(user_id: str) -> str:
    return f"{user_id}:active_account_id"


async def get_active_account_id(
    session: AsyncSession,
    user_id: str,
) -> uuid.UUID | None:
    """Return the user's active trading account id, or None for all-accounts.

    Self-healing: if the setting points at an account that no longer exists,
    was deactivated, or belongs to another user, it is treated as unset.
    """
    raw = await get_setting(session, _active_account_key(user_id))
    if not raw:
        return None
    try:
        account_id = uuid.UUID(raw)
    except ValueError:
        return None
    account = await session.get(Account, account_id)
    if account is None or account.user_id != user_id or not account.is_active:
        return None
    return account_id


async def set_active_account_id(
    session: AsyncSession,
    user_id: str,
    account_id: uuid.UUID | None,
) -> None:
    """Set (or clear, with None) the active trading account. Does not commit."""
    if account_id is None:
        await del_setting(session, _active_account_key(user_id))
        return
    account = await session.get(Account, account_id)
    if account is None or account.user_id != user_id or not account.is_active:
        raise ValueError("Account not found.")
    await set_setting(session, _active_account_key(user_id), str(account_id))


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

    await capture_equity_snapshots(session, user_id, source="sync")
    await session.commit()

    # Reload with balances
    for account in synced:
        await session.refresh(account, ["balances"])

    return synced


async def capture_equity_snapshots(
    session: AsyncSession,
    user_id: str,
    *,
    source: str = "sync",
) -> int:
    """Upsert today's EquitySnapshot per (account, currency) from the current
    AccountBalance rows. Last write of the day wins. Does not commit."""
    result = await session.execute(
        select(AccountBalance, Account).join(Account).where(
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        )
    )
    pairs = result.all()
    if not pairs:
        return 0
    today = datetime.now(timezone.utc).date()
    rows = [
        {
            "user_id": user_id,
            "account_id": account.id,
            "currency": bal.currency,
            "snapshot_date": datetime(today.year, today.month, today.day),
            "cash": bal.cash,
            "market_value": bal.market_value,
            "total_equity": bal.total_equity,
            "source": source,
        }
        for bal, account in pairs
    ]
    stmt = pg_insert(EquitySnapshot.__table__).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_equity_snapshot_account_ccy_date",
        set_={
            "cash": stmt.excluded.cash,
            "market_value": stmt.excluded.market_value,
            "total_equity": stmt.excluded.total_equity,
            "source": stmt.excluded.source,
        },
    )
    await session.execute(stmt)
    return len(rows)


async def get_peak_and_current_equity(
    session: AsyncSession,
    user_id: str,
) -> dict[str, tuple[Decimal, Decimal]]:
    """Per-currency (peak, current) equity for the drawdown breaker.

    Scope follows the active-account setting (the whole point: the RRSP
    experiment's drawdown must not be diluted by other accounts). Peak = max
    of daily per-currency sums across snapshot history; current = live
    AccountBalance totals. History accrues forward from the first snapshot —
    a pre-app peak is invisible until manually seeded.
    """
    account_id = await get_active_account_id(session, user_id)

    snap_stmt = select(
        EquitySnapshot.snapshot_date,
        EquitySnapshot.currency,
        func.sum(EquitySnapshot.total_equity),
    ).join(Account, Account.id == EquitySnapshot.account_id).where(
        Account.is_active == True,  # noqa: E712
        EquitySnapshot.user_id == user_id,
    ).group_by(EquitySnapshot.snapshot_date, EquitySnapshot.currency)
    if account_id is not None:
        snap_stmt = snap_stmt.where(EquitySnapshot.account_id == account_id)

    peaks: dict[str, Decimal] = {}
    for _date, currency, total in (await session.execute(snap_stmt)).all():
        if total is not None and (currency not in peaks or total > peaks[currency]):
            peaks[currency] = total

    current = await get_household_equity(session, user_id, account_id=account_id)

    out: dict[str, tuple[Decimal, Decimal]] = {}
    for currency in set(peaks) | set(current):
        cur = current.get(currency, Decimal(0))
        peak = max(peaks.get(currency, Decimal(0)), cur)
        out[currency] = (peak, cur)
    return out


async def get_household_equity(
    session: AsyncSession,
    user_id: str = "user_default",
    *,
    account_id: uuid.UUID | None = None,
    scope_to_active: bool = True,
) -> dict[str, Decimal]:
    """Sum total_equity by currency for a user's accounts.

    Scope resolution: an explicit `account_id` wins (e.g. sizing against the
    ticket's own account); otherwise the user's active-account setting applies
    unless `scope_to_active=False` (true household view, all accounts).
    """
    if account_id is None and scope_to_active:
        account_id = await get_active_account_id(session, user_id)
    stmt = select(AccountBalance).join(Account).where(
        Account.is_active == True,  # noqa: E712
        Account.user_id == user_id,
    )
    if account_id is not None:
        stmt = stmt.where(Account.id == account_id)
    result = await session.execute(stmt)
    balances = result.scalars().all()
    totals: dict[str, Decimal] = {}
    for b in balances:
        totals[b.currency] = totals.get(b.currency, Decimal(0)) + b.total_equity
    return totals
