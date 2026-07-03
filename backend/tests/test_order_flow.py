"""Order state machine against the paper broker: armed -> filled -> closed,
GTC stop placement, default exit ladder, trigger logic, ticket expiry."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.brokers.paper import PaperBroker, _paper_orders
from app.brokers.base import BrokerQuote
from app.db.models import (
    Fill,
    Order,
    OrderIntent,
    TicketStatus,
    TriggerType,
)
from app.services.monitor_service import MonitorService
from app.services.order_service import close_ticket, on_entry_filled, place_entry_order
from app.services.streak_service import get_snapshot
from tests.factories import make_account, make_ticket

USER = "order_user"


def _quote(last: str, volume: int | None = None) -> BrokerQuote:
    return BrokerQuote(
        symbol="TEST",
        last=Decimal(last),
        bid=None,
        ask=None,
        volume=volume,
        at=datetime.now(timezone.utc),
    )


async def _armed_ticket(session, **kwargs):
    account = await make_account(session, user_id=USER, currency="USD")
    ticket = await make_ticket(session, account, **kwargs)
    return account, ticket


async def test_paper_entry_fill_places_stop_and_exit_ladder(db_session):
    _paper_orders.clear()
    _, ticket = await _armed_ticket(db_session)
    broker = PaperBroker()

    entry = await place_entry_order(db_session, ticket, broker, last_price=Decimal("100.50"))
    await db_session.flush()  # sessions run autoflush=False; make the Fill queryable
    assert entry.intent == OrderIntent.ENTRY.value
    assert entry.is_paper

    # Paper broker fills instantly at last price (market order, no limit/stop set).
    fill = (
        await db_session.execute(select(Fill).where(Fill.order_id == entry.id))
    ).scalar_one()
    assert fill.price == Decimal("100.50")
    assert fill.quantity == 100

    await on_entry_filled(db_session, ticket, entry, fill.price, broker)

    assert ticket.status == TicketStatus.FILLED.value
    assert ticket.filled_at is not None

    # GTC stop-loss order exists at the ticket's stop.
    stop = (
        await db_session.execute(
            select(Order).where(
                Order.ticket_id == ticket.id,
                Order.intent == OrderIntent.STOP_LOSS.value,
            )
        )
    ).scalar_one()
    assert stop.stop_price == ticket.stop_price
    assert stop.side == "sell"

    # Default Minervini exit ladder: thirds at +1.5R / +2.5R / +4R.
    # Risk = 100.50 - 95 = 5.50.
    plan = ticket.exit_plan
    assert plan is not None
    targets = plan["targets"]
    assert [t["label"] for t in targets] == ["T1 +1.5R", "T2 +2.5R", "T3 +4R"]
    assert [t["shares"] for t in targets] == [33, 33, 34]
    assert float(targets[0]["price"]) == 108.75   # 100.50 + 1.5 x 5.50


async def test_close_at_stop_is_minus_one_r_and_updates_owner_streak(db_session):
    _paper_orders.clear()
    _, ticket = await _armed_ticket(db_session)
    broker = PaperBroker()
    entry = await place_entry_order(db_session, ticket, broker, last_price=Decimal("100"))
    await on_entry_filled(db_session, ticket, entry, Decimal("100"), broker)

    await close_ticket(db_session, ticket, exit_price=Decimal("95"), exit_reason="stop_hit")

    assert ticket.status == TicketStatus.STOPPED_OUT.value
    assert ticket.outcome == "loss"
    assert ticket.r_multiple == Decimal("-1.00")           # (95-100)/(100-95)
    assert ticket.realized_pnl == Decimal("-500.00")       # -5 x 100 shares

    # Regression: streak must update the ticket OWNER, not user_default.
    owner = await get_snapshot(db_session, user_id=USER)
    stranger = await get_snapshot(db_session, user_id="user_default")
    assert owner.consecutive_losses == 1
    assert stranger.consecutive_losses == 0


async def test_close_at_target_is_win(db_session):
    _paper_orders.clear()
    _, ticket = await _armed_ticket(db_session)
    broker = PaperBroker()
    entry = await place_entry_order(db_session, ticket, broker, last_price=Decimal("100"))
    await on_entry_filled(db_session, ticket, entry, Decimal("100"), broker)

    await close_ticket(db_session, ticket, exit_price=Decimal("115"), exit_reason="target_hit")

    assert ticket.status == TicketStatus.TARGET_HIT.value
    assert ticket.outcome == "win"
    assert ticket.r_multiple == Decimal("3.00")            # (115-100)/5


async def test_is_triggered_price_and_volume_matrix(db_session):
    _, ticket = await _armed_ticket(db_session)
    monitor = MonitorService(PaperBroker())

    # Plain price-above trigger at 100.
    ticket.trigger_type = TriggerType.PRICE_ABOVE.value
    assert not monitor._is_triggered(ticket, _quote("99.99"))
    assert monitor._is_triggered(ticket, _quote("100.00"))
    assert monitor._is_triggered(ticket, _quote("104.00"))

    # Volume-confirmed trigger: price below trigger short-circuits regardless.
    ticket.trigger_type = TriggerType.PRICE_ABOVE_WITH_VOLUME.value
    ticket.volume_confirm_multiple = 1.5
    assert not monitor._is_triggered(ticket, _quote("99.00", volume=10_000_000), avg_vol=1_000_000)
    # Missing average volume confirms rather than blocks (documented behavior).
    assert monitor._is_triggered(ticket, _quote("101.00", volume=10_000_000), avg_vol=None)


async def test_expire_stale_tickets(db_session):
    account = await make_account(db_session, user_id=USER)
    stale = await make_ticket(db_session, account, symbol="OLD")
    stale.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    fresh = await make_ticket(db_session, account, symbol="NEW")
    await db_session.flush()

    monitor = MonitorService(PaperBroker())
    await monitor._expire_stale_tickets(db_session)

    assert stale.status == TicketStatus.EXPIRED.value
    assert fresh.status == TicketStatus.ARMED.value
