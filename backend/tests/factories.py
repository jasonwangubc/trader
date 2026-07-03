"""Plain async factory helpers for seeding test rows."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountBalance, Ticket, TicketStatus


async def make_account(
    session: AsyncSession,
    *,
    user_id: str = "test_user",
    account_type: str = "RRSP",
    currency: str = "CAD",
    equity: Decimal = Decimal("50000"),
    cash: Decimal | None = None,
    questrade_account_id: str | None = None,
    is_active: bool = True,
) -> Account:
    """Create an Account with one AccountBalance in `currency`."""
    account = Account(
        user_id=user_id,
        questrade_account_id=questrade_account_id or uuid.uuid4().hex[:8],
        type=account_type,
        primary_currency=currency,
        real_money_enabled=False,
        is_active=is_active,
    )
    session.add(account)
    await session.flush()
    session.add(
        AccountBalance(
            account_id=account.id,
            currency=currency,
            cash=cash if cash is not None else equity,
            market_value=Decimal(0),
            total_equity=equity,
            buying_power=equity,
        )
    )
    await session.flush()
    return account


async def make_ticket(
    session: AsyncSession,
    account: Account,
    *,
    symbol: str = "TEST",
    status: str = TicketStatus.ARMED.value,
    trigger: Decimal = Decimal("100"),
    stop: Decimal = Decimal("95"),
    target: Decimal | None = Decimal("115"),
    shares: int = 100,
    is_paper: bool = True,
    closed_at: datetime | None = None,
    closed_hours_ago: float | None = None,
) -> Ticket:
    now = datetime.now(timezone.utc)
    if closed_hours_ago is not None:
        closed_at = now - timedelta(hours=closed_hours_ago)
    ticket = Ticket(
        user_id=account.user_id,
        account_id=account.id,
        symbol=symbol,
        currency=account.primary_currency,
        setup_type="VCP",
        trigger_type="price_above",
        trigger_price=trigger,
        stop_price=stop,
        target_price=target,
        risk_pct=Decimal("0.0075"),
        risk_amount=(trigger - stop) * shares,
        household_equity_at_creation=Decimal("50000"),
        streak_multiplier_at_creation=Decimal("1.00"),
        position_size_shares=shares,
        position_size_value=trigger * shares,
        status=status,
        is_paper=is_paper,
        armed_at=now,
        expires_at=now + timedelta(days=7),
        closed_at=closed_at,
        thesis="test thesis long enough",
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def add_balance(
    session: AsyncSession,
    account: Account,
    *,
    currency: str,
    equity: Decimal,
    cash: Decimal | None = None,
) -> AccountBalance:
    """Add another per-currency balance row to an existing account."""
    bal = AccountBalance(
        account_id=account.id,
        currency=currency,
        cash=cash if cash is not None else equity,
        market_value=Decimal(0),
        total_equity=equity,
        buying_power=equity,
    )
    session.add(bal)
    await session.flush()
    return bal
