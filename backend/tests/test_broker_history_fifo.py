"""FIFO round-trip matcher: lot consumption, commission allocation, idempotency."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import BrokerExecution, BrokerTrade
from app.services.broker_history_service import rebuild_trades_for_user
from tests.factories import make_account

USER = "fifo_user"
T0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


def _execution(
    account_id,
    *,
    side: str,
    qty: str,
    price: str,
    commission: str = "0",
    days: int = 0,
    symbol: str = "NVDA",
) -> BrokerExecution:
    return BrokerExecution(
        user_id=USER,
        account_id=account_id,
        broker_execution_id=uuid.uuid4().hex[:16],
        symbol=symbol,
        currency="USD",
        side=side,
        quantity=Decimal(qty),
        price=Decimal(price),
        commission=Decimal(commission),
        executed_at=T0 + timedelta(days=days),
    )


async def _trades(session) -> list[BrokerTrade]:
    q = await session.execute(
        select(BrokerTrade)
        .where(BrokerTrade.user_id == USER)
        .order_by(BrokerTrade.exit_date, BrokerTrade.avg_entry_price)
    )
    return list(q.scalars().all())


async def test_single_round_trip_net_of_commissions(db_session):
    account = await make_account(db_session, user_id=USER, currency="USD")
    db_session.add_all([
        _execution(account.id, side="buy", qty="100", price="50", commission="4.95", days=0),
        _execution(account.id, side="sell", qty="100", price="60", commission="4.95", days=10),
    ])
    await db_session.commit()

    n = await rebuild_trades_for_user(db_session, user_id=USER)
    assert n == 1
    (t,) = await _trades(db_session)
    assert t.shares == Decimal("100")
    assert t.avg_entry_price == Decimal("50")
    assert t.avg_exit_price == Decimal("60")
    # (60-50)*100 - 4.95 - 4.95 = 990.10
    assert t.realized_pnl == Decimal("990.100000")
    assert t.hold_days == 10


async def test_one_sell_consumes_two_lots_with_proportional_commissions(db_session):
    account = await make_account(db_session, user_id=USER, currency="USD")
    db_session.add_all([
        _execution(account.id, side="buy", qty="60", price="50", commission="6", days=0),
        _execution(account.id, side="buy", qty="40", price="55", commission="4", days=1),
        _execution(account.id, side="sell", qty="100", price="60", commission="10", days=5),
    ])
    await db_session.commit()

    n = await rebuild_trades_for_user(db_session, user_id=USER)
    assert n == 2  # one BrokerTrade per source lot
    lot1, lot2 = await _trades(db_session)

    # Lot 1: 60 sh @ 50 -> 60. Entry comm 6 (full lot), exit comm 10 * 60/100 = 6.
    assert lot1.shares == Decimal("60")
    assert lot1.avg_entry_price == Decimal("50")
    assert lot1.realized_pnl == Decimal("588.000000")   # 600 - 6 - 6

    # Lot 2: 40 sh @ 55 -> 60. Entry comm 4, exit comm 10 * 40/100 = 4.
    assert lot2.shares == Decimal("40")
    assert lot2.avg_entry_price == Decimal("55")
    assert lot2.realized_pnl == Decimal("192.000000")   # 200 - 4 - 4


async def test_scale_out_produces_one_trade_per_sell(db_session):
    account = await make_account(db_session, user_id=USER, currency="USD")
    db_session.add_all([
        _execution(account.id, side="buy", qty="90", price="100", days=0),
        _execution(account.id, side="sell", qty="30", price="110", days=3),
        _execution(account.id, side="sell", qty="30", price="120", days=6),
        _execution(account.id, side="sell", qty="30", price="90", days=9),
    ])
    await db_session.commit()

    n = await rebuild_trades_for_user(db_session, user_id=USER)
    assert n == 3
    t1, t2, t3 = await _trades(db_session)
    assert [t.realized_pnl for t in (t1, t2, t3)] == [
        Decimal("300.000000"), Decimal("600.000000"), Decimal("-300.000000"),
    ]
    # Partial exits leave the remaining shares untouched until sold.
    assert sum(t.shares for t in (t1, t2, t3)) == Decimal("90")


async def test_orphan_sell_is_skipped(db_session):
    account = await make_account(db_session, user_id=USER, currency="USD")
    db_session.add_all([
        # Sell with no prior buy (pre-window history) + a normal round trip.
        _execution(account.id, side="sell", qty="50", price="80", days=0, symbol="ORPH"),
        _execution(account.id, side="buy", qty="10", price="20", days=1),
        _execution(account.id, side="sell", qty="10", price="25", days=2),
    ])
    await db_session.commit()

    n = await rebuild_trades_for_user(db_session, user_id=USER)
    assert n == 1
    (t,) = await _trades(db_session)
    assert t.symbol == "NVDA"


async def test_rebuild_is_idempotent(db_session):
    account = await make_account(db_session, user_id=USER, currency="USD")
    db_session.add_all([
        _execution(account.id, side="buy", qty="100", price="50", days=0),
        _execution(account.id, side="sell", qty="100", price="60", days=10),
    ])
    await db_session.commit()

    first = await rebuild_trades_for_user(db_session, user_id=USER)
    second = await rebuild_trades_for_user(db_session, user_id=USER)
    assert first == second == 1
    trades = await _trades(db_session)
    assert len(trades) == 1  # wipe-and-rebuild, no duplicates


async def test_fifo_is_per_account(db_session):
    """A buy in one account must not match a sell in another (documented
    Questrade constraint: cross-account trades don't reconcile)."""
    rrsp = await make_account(db_session, user_id=USER, currency="USD", account_type="RRSP")
    tfsa = await make_account(db_session, user_id=USER, currency="USD", account_type="TFSA")
    db_session.add_all([
        _execution(rrsp.id, side="buy", qty="100", price="50", days=0),
        _execution(tfsa.id, side="sell", qty="100", price="60", days=5),
    ])
    await db_session.commit()

    n = await rebuild_trades_for_user(db_session, user_id=USER)
    assert n == 0  # orphan sell in TFSA; RRSP lot stays open
