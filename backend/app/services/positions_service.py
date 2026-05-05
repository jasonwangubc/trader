"""Positions sync + buying-power breakdown.

Cash-equivalents are positions in money-market / T-bill ETFs that act as parked
cash — sellable in a single trading day with negligible price risk. We tag them
so the UI can show the user how much capital is *freeable* (vs. tied up in
real positions) when sizing a new ticket.

The allowlist is intentionally hardcoded for the MVP. Easy to refine later via
a settings table or a manual override flag on Position.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface
from app.db.models import Account, AccountBalance, Position
from app.services.audit_service import log_event


# USD T-bill / cash-park ETFs + Canadian HISA/MM ETFs commonly used to park CAD.
# Symbol forms here match Questrade's convention (no exchange suffix for US,
# .TO for TSX, .NE for NEO).
CASH_EQUIVALENT_SYMBOLS: frozenset[str] = frozenset(
    {
        # USD T-bill / ultra-short
        "BIL", "SHV", "SGOV", "TBIL", "BOXX", "USFR", "ICSH", "GBIL",
        # CAD HISA / money-market ETFs
        "CASH.TO", "CASH.NE", "PSA.TO", "HISA.TO", "HISA.NE",
        "ZMMK.TO", "ZST.TO", "MNY.TO", "CSAV.TO", "HSAV.TO",
        "PSU.U.TO",  # USD-denominated US Treasury HISA listed on TSX
    }
)


def is_cash_equivalent(symbol: str) -> bool:
    return symbol.upper() in CASH_EQUIVALENT_SYMBOLS


async def sync_positions(session: AsyncSession, broker: BrokerInterface) -> list[Position]:
    """Pull positions for every active account and upsert into DB.

    Stale rows (positions that no longer exist at the broker) are deleted so
    the cached view matches reality.
    """
    accounts_result = await session.execute(
        select(Account).where(Account.is_active == True)  # noqa: E712
    )
    accounts = accounts_result.scalars().all()

    all_positions: list[Position] = []
    now = datetime.now(timezone.utc)

    for account in accounts:
        broker_positions = await broker.get_positions(account.questrade_account_id)
        seen_keys: set[tuple[str, str]] = set()

        for bp in broker_positions:
            seen_keys.add((bp.symbol, bp.currency))
            existing = await session.execute(
                select(Position).where(
                    Position.account_id == account.id,
                    Position.symbol == bp.symbol,
                    Position.currency == bp.currency,
                )
            )
            pos = existing.scalar_one_or_none()
            if pos is None:
                pos = Position(
                    account_id=account.id,
                    symbol=bp.symbol,
                    currency=bp.currency,
                )
                session.add(pos)

            pos.quantity = bp.quantity
            pos.avg_cost = bp.avg_cost
            pos.current_price = bp.current_price
            pos.market_value = bp.market_value
            pos.open_pnl = bp.open_pnl
            pos.as_of = now
            all_positions.append(pos)

        # Drop stale rows for this account (positions closed at the broker).
        stale_result = await session.execute(
            select(Position).where(Position.account_id == account.id)
        )
        for existing in stale_result.scalars().all():
            if (existing.symbol, existing.currency) not in seen_keys:
                await session.delete(existing)

    await log_event(
        session,
        actor="system",
        event_type="positions_synced",
        payload={"count": len(all_positions)},
    )
    await session.commit()
    return all_positions


async def list_positions(session: AsyncSession) -> list[Position]:
    result = await session.execute(
        select(Position)
        .join(Account)
        .where(Account.is_active == True)  # noqa: E712
        .order_by(Position.symbol)
    )
    return list(result.scalars().all())


async def buying_power_breakdown(
    session: AsyncSession, *, currency: str
) -> dict[str, Decimal]:
    """Return {cash, cash_equivalents, freeable_total} aggregated across accounts
    for the given currency.

    - cash: sum of AccountBalance.cash for the currency
    - cash_equivalents: sum of market_value of positions flagged is_cash_equivalent
    - freeable_total: cash + cash_equivalents (what could be deployed by EOD)
    """
    bal_result = await session.execute(
        select(AccountBalance)
        .join(Account)
        .where(
            AccountBalance.currency == currency,
            Account.is_active == True,  # noqa: E712
        )
    )
    cash = sum(
        (b.cash for b in bal_result.scalars().all()),
        start=Decimal(0),
    )

    pos_result = await session.execute(
        select(Position)
        .join(Account)
        .where(
            Position.currency == currency,
            Account.is_active == True,  # noqa: E712
        )
    )
    cash_equiv = Decimal(0)
    for p in pos_result.scalars().all():
        if is_cash_equivalent(p.symbol):
            cash_equiv += p.market_value

    return {
        "cash": cash,
        "cash_equivalents": cash_equiv,
        "freeable_total": cash + cash_equiv,
    }
