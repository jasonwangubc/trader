"""Positions sync + buying-power breakdown.

Cash-equivalents are positions in money-market / T-bill ETFs that act as parked
cash — sellable in a single trading day with negligible price risk. We tag them
so the UI can show the user how much capital is *freeable* (vs. tied up in
real positions) when sizing a new ticket.

The allowlist is intentionally hardcoded for the MVP. Easy to refine later via
a settings table or a manual override flag on Position.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface, BrokerOpenOrder
from app.db.models import Account, AccountBalance, Position
from app.services.accounts_service import get_active_account_id
from app.services.audit_service import log_event

log = logging.getLogger(__name__)


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


async def sync_positions(
    session: AsyncSession,
    broker: BrokerInterface,
    user_id: str = "user_default",
) -> list[Position]:
    """Pull positions for every active account of this user and upsert into DB.

    Stale rows (positions that no longer exist at the broker) are deleted so
    the cached view matches reality.
    """
    accounts_result = await session.execute(
        select(Account).where(
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        )
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


async def list_positions(
    session: AsyncSession,
    user_id: str = "user_default",
) -> list[Position]:
    stmt = (
        select(Position)
        .join(Account)
        .where(
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        )
        .order_by(Position.symbol)
    )
    active_id = await get_active_account_id(session, user_id)
    if active_id is not None:
        stmt = stmt.where(Account.id == active_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@dataclass
class BrokerStopTarget:
    """Best-effort stop / target derived from a position's broker-side open orders.

    Computed from sell-side open orders only. The lowest sell-stop becomes the
    stop; the highest sell-limit becomes the target. Multiple stops would
    indicate a partial-exit ladder — we take the lowest (most protective)
    for the headline.
    """
    stop_price: Decimal | None
    target_price: Decimal | None
    open_order_count: int


async def fetch_broker_stop_targets(
    *,
    user_id: str,
    session: AsyncSession,
    broker: BrokerInterface,
) -> dict[tuple[uuid.UUID, str], BrokerStopTarget]:
    """For each (account_id, symbol) tied to an active account, derive the
    sell-side stop and target from the broker's currently-open orders.

    Returns an empty map if the broker can't list orders or any account fails.
    Never raises — this is a best-effort annotation, not authoritative data.
    """
    out: dict[tuple[uuid.UUID, str], BrokerStopTarget] = {}
    accounts_stmt = select(Account).where(
        Account.is_active == True,  # noqa: E712
        Account.user_id == user_id,
    )
    active_id = await get_active_account_id(session, user_id)
    if active_id is not None:
        accounts_stmt = accounts_stmt.where(Account.id == active_id)
    accounts_q = await session.execute(accounts_stmt)
    for acct in accounts_q.scalars().all():
        try:
            orders = await broker.get_open_orders(acct.questrade_account_id)
        except Exception as exc:
            log.debug("get_open_orders failed for %s: %s", acct.questrade_account_id, exc)
            continue

        per_symbol: dict[str, list[BrokerOpenOrder]] = {}
        for o in orders:
            if o.side.lower() != "sell":
                continue
            per_symbol.setdefault(o.symbol, []).append(o)

        for symbol, sym_orders in per_symbol.items():
            stops = [o.stop_price for o in sym_orders if o.stop_price is not None and "stop" in o.order_type]
            targets = [o.limit_price for o in sym_orders if o.limit_price is not None and o.order_type == "limit"]
            out[(acct.id, symbol)] = BrokerStopTarget(
                stop_price=min(stops) if stops else None,
                target_price=max(targets) if targets else None,
                open_order_count=len(sym_orders),
            )
    return out


async def buying_power_breakdown(
    session: AsyncSession,
    *,
    currency: str,
    user_id: str = "user_default",
    account_id: uuid.UUID | None = None,
) -> dict[str, Decimal]:
    """Return {cash, cash_equivalents, freeable_total} for this user's accounts.

    Pass `account_id` to scope to one account (e.g. the ticket's own account);
    otherwise the user's active-account setting applies when set.
    """
    if account_id is None:
        account_id = await get_active_account_id(session, user_id)

    bal_stmt = (
        select(AccountBalance)
        .join(Account)
        .where(
            AccountBalance.currency == currency,
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        )
    )
    if account_id is not None:
        bal_stmt = bal_stmt.where(Account.id == account_id)
    bal_result = await session.execute(bal_stmt)
    cash = sum(
        (b.cash for b in bal_result.scalars().all()),
        start=Decimal(0),
    )

    pos_stmt = (
        select(Position)
        .join(Account)
        .where(
            Position.currency == currency,
            Account.is_active == True,  # noqa: E712
            Account.user_id == user_id,
        )
    )
    if account_id is not None:
        pos_stmt = pos_stmt.where(Account.id == account_id)
    pos_result = await session.execute(pos_stmt)
    cash_equiv = Decimal(0)
    for p in pos_result.scalars().all():
        if is_cash_equivalent(p.symbol):
            cash_equiv += p.market_value

    return {
        "cash": cash,
        "cash_equivalents": cash_equiv,
        "freeable_total": cash + cash_equiv,
    }
