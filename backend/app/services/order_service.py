"""Order lifecycle: place entry order on trigger, place stop on fill, poll live orders.

State machine per ticket:
  armed ──(trigger fires)──► triggered ──(entry fills)──► filled
                                              │
                                              └──► stop order placed (GTC)

For paper tickets the entry fill is instant; for live Questrade tickets the
monitor polls get_order() each tick until the order is filled or cancelled.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface, BrokerOrderRequest, BrokerQuote
from app.db.models import (
    Account,
    Fill,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    Ticket,
    TicketStatus,
    TradeOutcome,
)
from app.services.audit_service import log_event
from app.services.notifications_service import alert_stopped_out, alert_target_hit
from app.services.streak_service import record_outcome

log = logging.getLogger(__name__)


async def place_entry_order(
    session: AsyncSession,
    ticket: Ticket,
    broker: BrokerInterface,
    last_price: Decimal,
) -> Order:
    """Place a stop-limit entry buy.

    For live orders we use a STOP-LIMIT rather than MARKET:
      stop_price  = trigger price   (order activates when price reaches here)
      limit_price = trigger × 1.005 (won't pay more than 0.5% above — prevents
                                     chasing gaps; if stock opens above limit
                                     the order simply doesn't fill)

    For paper orders the broker simulates an instant fill at last_price.
    """
    account = await session.get(Account, ticket.account_id)
    if account is None:
        raise RuntimeError(f"Account {ticket.account_id} not found")

    # ── Buying-power pre-flight check for live accounts ───────────────────
    if not ticket.is_paper:
        from sqlalchemy import select as _select
        from app.db.models import AccountBalance as _AB
        bp_result = await session.execute(
            _select(_AB).where(
                _AB.account_id == ticket.account_id,
                _AB.currency == ticket.currency,
            )
        )
        balance = bp_result.scalar_one_or_none()
        required = ticket.position_size_value
        available = balance.buying_power if balance else Decimal(0)
        if available < required:
            raise RuntimeError(
                f"Insufficient buying power for {ticket.symbol}: "
                f"need {ticket.currency} {float(required):.0f}, "
                f"have {float(available):.0f}. "
                f"Sell TBIL or other cash-equivalents first (T+1 settlement)."
            )

    # Use stop-limit for live; paper broker handles market-style simulation internally
    trigger = ticket.trigger_price
    limit   = (trigger * Decimal("1.005")).quantize(Decimal("0.01"))  # 0.5% ceiling

    req = BrokerOrderRequest(
        account_id=account.questrade_account_id,
        symbol=ticket.symbol,
        side="buy",
        order_type="stop_limit" if not ticket.is_paper else "market",
        quantity=ticket.position_size_shares,
        stop_price=trigger if not ticket.is_paper else None,
        limit_price=limit if not ticket.is_paper else None,
        time_in_force="Day",
    )
    ack = await broker.place_order(req)
    log.info("Entry order placed: %s %s x%d → broker_id=%s status=%s",
             ticket.symbol, account.questrade_account_id,
             ticket.position_size_shares, ack.broker_order_id, ack.status)

    order = Order(
        ticket_id=ticket.id,
        account_id=ticket.account_id,
        questrade_order_id=ack.broker_order_id,
        symbol=ticket.symbol,
        currency=ticket.currency,
        side=OrderSide.BUY.value,
        order_type=OrderType.STOP_LIMIT.value if not ticket.is_paper else OrderType.MARKET.value,
        intent=OrderIntent.ENTRY.value,
        quantity=ticket.position_size_shares,
        limit_price=limit if not ticket.is_paper else None,
        stop_price=trigger if not ticket.is_paper else None,
        status=OrderStatus.SUBMITTED.value,
        is_paper=ticket.is_paper,
        submitted_at=ack.submitted_at,
    )
    session.add(order)
    await session.flush()  # populate order.id

    # Paper broker fills instantly.
    if ack.status == "filled":
        fill_price = ack.fill_price or last_price
        await _record_fill(session, order, fill_price, ack.fill_quantity or ticket.position_size_shares)

    await log_event(
        session,
        actor="system",
        event_type="entry_order_placed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "broker_order_id": ack.broker_order_id,
            "symbol": ticket.symbol,
            "quantity": ticket.position_size_shares,
            "status": ack.status,
            "is_paper": ticket.is_paper,
        },
    )
    return order


async def place_stop_order(
    session: AsyncSession,
    ticket: Ticket,
    broker: BrokerInterface,
) -> Order:
    """Place the hard stop-loss (GTC stop-market sell). Called once entry fills."""
    account = await session.get(Account, ticket.account_id)
    if account is None:
        raise RuntimeError(f"Account {ticket.account_id} not found")

    req = BrokerOrderRequest(
        account_id=account.questrade_account_id,
        symbol=ticket.symbol,
        side="sell",
        order_type="stop_market",
        quantity=ticket.position_size_shares,
        stop_price=ticket.stop_price,
        time_in_force="GoodTillCancelled",
    )
    ack = await broker.place_order(req)
    log.info("Stop order placed: %s stop=%s broker_id=%s",
             ticket.symbol, ticket.stop_price, ack.broker_order_id)

    order = Order(
        ticket_id=ticket.id,
        account_id=ticket.account_id,
        questrade_order_id=ack.broker_order_id,
        symbol=ticket.symbol,
        currency=ticket.currency,
        side=OrderSide.SELL.value,
        order_type=OrderType.STOP_MARKET.value,
        intent=OrderIntent.STOP_LOSS.value,
        quantity=ticket.position_size_shares,
        stop_price=ticket.stop_price,
        status=OrderStatus.SUBMITTED.value,
        is_paper=ticket.is_paper,
        submitted_at=ack.submitted_at,
    )
    session.add(order)
    await session.flush()

    await log_event(
        session,
        actor="system",
        event_type="stop_order_placed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "broker_order_id": ack.broker_order_id,
            "stop_price": str(ticket.stop_price),
            "quantity": ticket.position_size_shares,
            "is_paper": ticket.is_paper,
        },
    )
    return order


async def on_entry_filled(
    session: AsyncSession,
    ticket: Ticket,
    entry_order: Order,
    fill_price: Decimal,
    broker: BrokerInterface,
) -> None:
    """Transition ticket to FILLED and arm the stop order."""
    entry_order.status = OrderStatus.FILLED.value
    entry_order.filled_at = datetime.now(timezone.utc)

    ticket.status = TicketStatus.FILLED.value
    ticket.filled_at = datetime.now(timezone.utc)

    await log_event(
        session,
        actor="system",
        event_type="ticket_filled",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol": ticket.symbol,
            "fill_price": str(fill_price),
            "quantity": ticket.position_size_shares,
            "stop_price": str(ticket.stop_price),
            "is_paper": ticket.is_paper,
        },
    )

    await place_stop_order(session, ticket, broker)


async def check_open_entry_orders(
    session: AsyncSession, broker: BrokerInterface
) -> None:
    """Poll Questrade for any live entry orders that are still open, detect fills."""
    result = await session.execute(
        select(Order)
        .where(
            Order.intent == OrderIntent.ENTRY.value,
            Order.status.in_([OrderStatus.SUBMITTED.value, OrderStatus.ACCEPTED.value]),
            Order.is_paper == False,  # noqa: E712 — paper orders fill instantly
        )
    )
    open_orders = result.scalars().all()
    if not open_orders:
        return

    for order in open_orders:
        ticket = await session.get(Ticket, order.ticket_id)
        if ticket is None:
            continue
        account = await session.get(Account, order.account_id)
        if account is None:
            continue
        try:
            ack = await broker.get_order(account.questrade_account_id, order.questrade_order_id)
        except Exception:
            log.exception("Failed to check order %s for %s", order.questrade_order_id, order.symbol)
            continue

        order.status = ack.status
        if ack.status == "filled":
            fill_price = ack.fill_price or Decimal(str(ticket.trigger_price))
            fill_qty = ack.fill_quantity or order.quantity
            await _record_fill(session, order, fill_price, fill_qty)
            await on_entry_filled(session, ticket, order, fill_price, broker)
            log.info("Live entry fill confirmed: %s @ %s", order.symbol, fill_price)
        elif ack.status in ("cancelled", "rejected"):
            log.warning("Entry order %s for %s became %s", order.questrade_order_id, order.symbol, ack.status)


async def close_ticket(
    session: AsyncSession,
    ticket: Ticket,
    exit_price: Decimal,
    exit_reason: str,           # "stop_hit" | "target_hit" | "manual" | "time_stop"
    exit_quantity: int | None = None,
) -> None:
    """Close a filled ticket: compute R-multiple, record outcome, update streak."""
    qty = exit_quantity or ticket.position_size_shares

    # Find the entry fill price.
    entry_fill_result = await session.execute(
        select(Fill)
        .join(Order)
        .where(
            Order.ticket_id == ticket.id,
            Order.intent == OrderIntent.ENTRY.value,
            Fill.order_id == Order.id,
        )
        .order_by(Fill.occurred_at)
        .limit(1)
    )
    entry_fill = entry_fill_result.scalar_one_or_none()
    entry_price = entry_fill.price if entry_fill else ticket.trigger_price

    per_share_risk = entry_price - ticket.stop_price
    realized_pnl = (exit_price - entry_price) * qty
    r_multiple = (
        ((exit_price - entry_price) / per_share_risk).quantize(Decimal("0.01"))
        if per_share_risk > 0
        else Decimal(0)
    )

    if r_multiple > Decimal("0.10"):
        outcome = TradeOutcome.WIN
        new_status = TicketStatus.TARGET_HIT if exit_reason == "target_hit" else TicketStatus.FILLED
    elif r_multiple < Decimal("-0.05"):
        outcome = TradeOutcome.LOSS
        new_status = TicketStatus.STOPPED_OUT
    else:
        outcome = TradeOutcome.SCRATCH
        new_status = TicketStatus.FILLED

    if exit_reason == "stop_hit":
        new_status = TicketStatus.STOPPED_OUT
        outcome = TradeOutcome.LOSS
    elif exit_reason == "target_hit":
        new_status = TicketStatus.TARGET_HIT
        outcome = TradeOutcome.WIN

    ticket.status = new_status.value
    ticket.closed_at = datetime.now(timezone.utc)
    ticket.realized_pnl = realized_pnl.quantize(Decimal("0.01"))
    ticket.r_multiple = r_multiple
    ticket.outcome = outcome.value

    await record_outcome(session, outcome=outcome, ticket_id=ticket.id)

    await log_event(
        session,
        actor="system",
        event_type="ticket_closed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol": ticket.symbol,
            "exit_reason": exit_reason,
            "exit_price": str(exit_price),
            "entry_price": str(entry_price),
            "r_multiple": str(r_multiple),
            "realized_pnl": str(realized_pnl),
            "outcome": outcome.value,
            "is_paper": ticket.is_paper,
        },
    )
    log.info("Ticket closed: %s %s R=%.2f outcome=%s",
             ticket.symbol, exit_reason, r_multiple, outcome.value)

    if exit_reason == "stop_hit":
        alert_stopped_out(ticket.symbol, exit_price, ticket.is_paper)
    elif exit_reason == "target_hit":
        alert_target_hit(ticket.symbol, "target hit", exit_price, ticket.is_paper)


async def check_filled_tickets_for_close(
    session: AsyncSession,
    broker: BrokerInterface,
    quotes: dict[str, BrokerQuote],
) -> None:
    """For paper tickets: check if stop or target was hit. For live: poll GTC stop order."""
    result = await session.execute(
        select(Ticket).where(Ticket.status == TicketStatus.FILLED.value)
    )
    filled_tickets = result.scalars().all()
    if not filled_tickets:
        return

    for ticket in filled_tickets:
        if ticket.is_paper:
            quote = quotes.get(ticket.symbol)
            if quote is None:
                continue
            last = quote.last
            stop = ticket.stop_price
            target = ticket.target_price

            if last <= stop:
                await close_ticket(session, ticket, stop, "stop_hit")
            elif target and last >= target:
                await close_ticket(session, ticket, target, "target_hit")
            else:
                await _check_exit_ladder(session, ticket, last)
        else:
            # Live: poll the GTC stop-loss order for fills.
            await _check_live_stop_order(session, ticket, broker)


async def _check_live_stop_order(
    session: AsyncSession, ticket: Ticket, broker: BrokerInterface
) -> None:
    result = await session.execute(
        select(Order).where(
            Order.ticket_id == ticket.id,
            Order.intent == OrderIntent.STOP_LOSS.value,
            Order.status.in_([OrderStatus.SUBMITTED.value, OrderStatus.ACCEPTED.value]),
        )
    )
    stop_order = result.scalar_one_or_none()
    if stop_order is None:
        return

    account = await session.get(Account, ticket.account_id)
    if account is None:
        return

    try:
        ack = await broker.get_order(account.questrade_account_id, stop_order.questrade_order_id)
    except Exception:
        log.exception("Failed to poll stop order %s", stop_order.questrade_order_id)
        return

    stop_order.status = ack.status
    if ack.status == "filled":
        fill_price = ack.fill_price or ticket.stop_price
        fill_qty = ack.fill_quantity or ticket.position_size_shares
        await _record_fill(session, stop_order, fill_price, fill_qty)
        await close_ticket(session, ticket, fill_price, "stop_hit")


async def _check_exit_ladder(
    session: AsyncSession, ticket: Ticket, last: Decimal
) -> None:
    """Mark exit ladder legs as hit when price reaches them.
    Does NOT auto-place orders — the user reviews and closes manually or
    a future sprint adds partial order automation.
    """
    plan = ticket.exit_plan
    if not plan or not plan.get("targets"):
        return

    changed = False
    for leg in plan["targets"]:
        if leg.get("hit"):
            continue
        leg_price = Decimal(str(leg["price"]))
        if last >= leg_price:
            leg["hit"] = True
            changed = True
            log.info("Exit ladder target hit: %s %s @ %s (label: %s)",
                     ticket.symbol, ticket.id, leg_price, leg.get("label", ""))
            await log_event(
                session,
                actor="system",
                event_type="exit_target_hit",
                entity_type="ticket",
                entity_id=ticket.id,
                payload={
                    "symbol": ticket.symbol,
                    "target_price": str(leg_price),
                    "last_price": str(last),
                    "label": leg.get("label", ""),
                },
            )

    if changed:
        # Force SQLAlchemy to detect the JSONB mutation
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(ticket, "exit_plan")


async def _record_fill(
    session: AsyncSession, order: Order, price: Decimal, quantity: int
) -> None:
    fill = Fill(
        order_id=order.id,
        ticket_id=order.ticket_id,
        quantity=quantity,
        price=price,
        is_paper=order.is_paper,
    )
    session.add(fill)
    order.status = OrderStatus.FILLED.value
    order.filled_at = datetime.now(timezone.utc)
