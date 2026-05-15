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

from app.brokers.base import BrokerBracketRequest, BrokerInterface, BrokerOrderRequest, BrokerQuote
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
from app.services.notifications_service import alert_stopped_out, alert_target_hit, alert_trailing_action
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
    # 2% ceiling: prevents chasing a runaway gap but handles normal breakout
    # volatility. 0.5% was too tight — any overnight gap > 0.5% above the pivot
    # would activate the stop price but fail to fill the limit, missing the trade.
    # Minervini's proper buy zone is within 5% of the pivot; 2% is disciplined
    # without causing routine missed entries.
    limit   = (trigger * Decimal("1.02")).quantize(Decimal("0.01"))

    # Live path: try a bracket order so the stop is in place at the broker
    # the moment the entry fills (zero naked window). Fall back to sequential
    # place_order if the broker doesn't support brackets.
    if not ticket.is_paper:
        try:
            return await _place_entry_with_bracket(
                session, ticket, broker, account, trigger, limit,
            )
        except NotImplementedError:
            log.warning("Broker %s lacks bracket support; falling back to sequential orders", broker.name)

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


async def _place_entry_with_bracket(
    session: AsyncSession,
    ticket: Ticket,
    broker: BrokerInterface,
    account: Account,
    trigger: Decimal,
    limit: Decimal,
) -> Order:
    """Atomic entry+stop via broker bracket. Creates two Order rows in one shot:
    the entry (returned) and the GTC stop that activates server-side on fill.
    """
    req = BrokerBracketRequest(
        account_id=account.questrade_account_id,
        symbol=ticket.symbol,
        quantity=ticket.position_size_shares,
        entry_stop_price=trigger,
        entry_limit_price=limit,
        stop_loss_price=ticket.stop_price,
        entry_time_in_force="Day",
        stop_loss_time_in_force="GoodTillCancelled",
    )
    ack = await broker.place_bracket_order(req)
    log.info("Bracket placed: %s qty=%d entry=%s/%s stop=%s primary_id=%s stop_id=%s",
             ticket.symbol, ticket.position_size_shares,
             trigger, limit, ticket.stop_price,
             ack.primary.broker_order_id, ack.stop_loss.broker_order_id)

    entry_order = Order(
        ticket_id=ticket.id,
        account_id=ticket.account_id,
        questrade_order_id=ack.primary.broker_order_id,
        symbol=ticket.symbol,
        currency=ticket.currency,
        side=OrderSide.BUY.value,
        order_type=OrderType.STOP_LIMIT.value,
        intent=OrderIntent.ENTRY.value,
        quantity=ticket.position_size_shares,
        limit_price=limit,
        stop_price=trigger,
        status=OrderStatus.SUBMITTED.value,
        is_paper=False,
        submitted_at=ack.primary.submitted_at,
    )
    stop_order = Order(
        ticket_id=ticket.id,
        account_id=ticket.account_id,
        questrade_order_id=ack.stop_loss.broker_order_id,
        symbol=ticket.symbol,
        currency=ticket.currency,
        side=OrderSide.SELL.value,
        order_type=OrderType.STOP_MARKET.value,
        intent=OrderIntent.STOP_LOSS.value,
        quantity=ticket.position_size_shares,
        stop_price=ticket.stop_price,
        status=OrderStatus.SUBMITTED.value,
        is_paper=False,
        submitted_at=ack.stop_loss.submitted_at,
    )
    session.add(entry_order)
    session.add(stop_order)
    await session.flush()

    await log_event(
        session,
        actor="system",
        event_type="bracket_order_placed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "entry_broker_order_id": ack.primary.broker_order_id,
            "stop_broker_order_id": ack.stop_loss.broker_order_id,
            "symbol": ticket.symbol,
            "quantity": ticket.position_size_shares,
            "entry_stop_price": str(trigger),
            "entry_limit_price": str(limit),
            "stop_loss_price": str(ticket.stop_price),
        },
    )
    return entry_order


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


def _build_default_exit_plan(fill_price: Decimal, stop_price: Decimal, shares: int) -> dict:
    """Minervini default exit ladder: 1/3 at +1.5R, 1/3 at +2.5R, remainder at +4R.
    User can edit on the ticket detail page after fill.
    """
    risk = float(fill_price) - float(stop_price)
    if risk <= 0:
        return {}
    third = shares // 3
    return {"targets": [
        {"price": str(round(float(fill_price) + risk * 1.5, 2)), "shares": third,
         "label": "T1 +1.5R", "hit": False, "action_queued": False},
        {"price": str(round(float(fill_price) + risk * 2.5, 2)), "shares": third,
         "label": "T2 +2.5R", "hit": False, "action_queued": False},
        {"price": str(round(float(fill_price) + risk * 4.0, 2)), "shares": shares - 2 * third,
         "label": "T3 +4R", "hit": False, "action_queued": False},
    ]}


async def on_entry_filled(
    session: AsyncSession,
    ticket: Ticket,
    entry_order: Order,
    fill_price: Decimal,
    broker: BrokerInterface,
) -> None:
    """Transition ticket to FILLED, arm the stop order, and auto-set exit plan."""
    entry_order.status = OrderStatus.FILLED.value
    entry_order.filled_at = datetime.now(timezone.utc)

    ticket.status = TicketStatus.FILLED.value
    ticket.filled_at = datetime.now(timezone.utc)

    # Auto-populate the Minervini exit ladder if not already set.
    # User can override on the ticket detail page.
    if ticket.exit_plan is None:
        plan = _build_default_exit_plan(fill_price, ticket.stop_price, ticket.position_size_shares)
        if plan:
            ticket.exit_plan = plan

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

    # Skip stop placement if it's already at the broker (bracket-order path).
    # Cancelled/rejected stops don't count — fall through to place a fresh one.
    existing_stop_q = await session.execute(
        select(Order).where(
            Order.ticket_id == ticket.id,
            Order.intent == OrderIntent.STOP_LOSS.value,
            Order.status.notin_([OrderStatus.CANCELLED.value, OrderStatus.REJECTED.value]),
        )
    )
    if existing_stop_q.scalar_one_or_none() is None:
        await place_stop_order(session, ticket, broker)


async def replace_stop_order(
    session: AsyncSession,
    ticket: Ticket,
    new_stop_price: Decimal,
    broker: BrokerInterface,
) -> Order:
    """Cancel the current GTC stop-loss and place a new one at new_stop_price.

    Called when the user confirms a trail_stop TrailingAction.
    Updates ticket.stop_price in DB so future R-multiple calculations are correct.
    """
    account = await session.get(Account, ticket.account_id)
    if account is None:
        raise RuntimeError(f"Account {ticket.account_id} not found")

    # Find and cancel the current stop-loss order
    result = await session.execute(
        select(Order).where(
            Order.ticket_id == ticket.id,
            Order.intent == OrderIntent.STOP_LOSS.value,
            Order.status.in_([OrderStatus.SUBMITTED.value, OrderStatus.ACCEPTED.value]),
        )
    )
    old_stop_order = result.scalar_one_or_none()
    if old_stop_order is not None and not ticket.is_paper:
        try:
            await broker.cancel_order(account.questrade_account_id, old_stop_order.questrade_order_id)
            old_stop_order.status = OrderStatus.CANCELLED.value
            old_stop_order.cancelled_at = datetime.now(timezone.utc)
            log.info("Cancelled old stop order %s for %s", old_stop_order.questrade_order_id, ticket.symbol)
        except Exception:
            log.exception("Failed to cancel old stop order %s — continuing anyway", old_stop_order.questrade_order_id)

    # Update ticket stop price
    old_stop = ticket.stop_price
    ticket.stop_price = new_stop_price

    # Place new stop order (reuses full remaining shares)
    remaining_shares = ticket.position_size_shares
    req = BrokerOrderRequest(
        account_id=account.questrade_account_id,
        symbol=ticket.symbol,
        side="sell",
        order_type="stop_market",
        quantity=remaining_shares,
        stop_price=new_stop_price,
        time_in_force="GoodTillCancelled",
    )
    ack = await broker.place_order(req)
    log.info("New trailing stop placed: %s %s → %s broker_id=%s",
             ticket.symbol, old_stop, new_stop_price, ack.broker_order_id)

    new_order = Order(
        ticket_id=ticket.id,
        account_id=ticket.account_id,
        questrade_order_id=ack.broker_order_id,
        symbol=ticket.symbol,
        currency=ticket.currency,
        side=OrderSide.SELL.value,
        order_type=OrderType.STOP_MARKET.value,
        intent=OrderIntent.STOP_LOSS.value,
        quantity=remaining_shares,
        stop_price=new_stop_price,
        status=OrderStatus.SUBMITTED.value,
        is_paper=ticket.is_paper,
        submitted_at=ack.submitted_at,
    )
    session.add(new_order)
    await session.flush()

    await log_event(
        session,
        actor="user",
        event_type="stop_trailed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol": ticket.symbol,
            "old_stop": str(old_stop),
            "new_stop": str(new_stop_price),
            "broker_order_id": ack.broker_order_id,
            "is_paper": ticket.is_paper,
        },
    )
    return new_order


async def place_scale_out_order(
    session: AsyncSession,
    ticket: Ticket,
    sell_price: Decimal,
    sell_shares: int,
    broker: BrokerInterface,
) -> Order:
    """Place a limit sell order for a partial exit (exit ladder leg).

    Called when the user confirms a scale_out TrailingAction.
    Does NOT close the ticket — it remains FILLED with reduced effective size.
    The existing GTC stop continues to protect the remaining shares.
    """
    account = await session.get(Account, ticket.account_id)
    if account is None:
        raise RuntimeError(f"Account {ticket.account_id} not found")

    req = BrokerOrderRequest(
        account_id=account.questrade_account_id,
        symbol=ticket.symbol,
        side="sell",
        order_type="limit",
        quantity=sell_shares,
        limit_price=sell_price,
        time_in_force="Day",
    )
    ack = await broker.place_order(req)
    log.info("Scale-out order placed: %s %d sh @ %s broker_id=%s",
             ticket.symbol, sell_shares, sell_price, ack.broker_order_id)

    order = Order(
        ticket_id=ticket.id,
        account_id=ticket.account_id,
        questrade_order_id=ack.broker_order_id,
        symbol=ticket.symbol,
        currency=ticket.currency,
        side=OrderSide.SELL.value,
        order_type=OrderType.LIMIT.value,
        intent=OrderIntent.SCALE_OUT.value,
        quantity=sell_shares,
        limit_price=sell_price,
        status=OrderStatus.SUBMITTED.value,
        is_paper=ticket.is_paper,
        submitted_at=ack.submitted_at,
    )
    session.add(order)
    await session.flush()

    # Paper broker fills instantly — record the fill so callers can detect it.
    if ack.status == "filled":
        await _record_fill(session, order, ack.fill_price or sell_price, sell_shares)

    await log_event(
        session,
        actor="system",
        event_type="scale_out_placed",
        entity_type="ticket",
        entity_id=ticket.id,
        payload={
            "symbol": ticket.symbol,
            "sell_price": str(sell_price),
            "sell_shares": sell_shares,
            "broker_order_id": ack.broker_order_id,
            "is_paper": ticket.is_paper,
        },
    )
    return order


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
    """For paper tickets: check stop/target hits. For live: poll GTC stop order.
    For all filled tickets: check trailing milestones and queue coaching actions.
    """
    from app.services.trailing_service import check_and_queue_actions

    result = await session.execute(
        select(Ticket).where(Ticket.status == TicketStatus.FILLED.value)
    )
    filled_tickets = result.scalars().all()
    if not filled_tickets:
        return

    for ticket in filled_tickets:
        quote = quotes.get(ticket.symbol)
        last = quote.last if quote else None

        if ticket.is_paper:
            if last is None:
                continue
            stop   = ticket.stop_price
            target = ticket.target_price
            if last <= stop:
                await close_ticket(session, ticket, stop, "stop_hit")
                continue
            elif target and last >= target:
                await close_ticket(session, ticket, target, "target_hit")
                continue
            else:
                await _check_exit_ladder(session, ticket, last, broker)
        else:
            # Live: poll GTC stop for fills, then check exit ladder for scale-outs.
            await _check_live_stop_order(session, ticket, broker)
            if ticket.status == TicketStatus.FILLED.value and last is not None:
                await _check_scale_out_fills(session, ticket, broker)
                if ticket.status == TicketStatus.FILLED.value:
                    await _check_exit_ladder(session, ticket, last, broker)

        # Queue trailing actions for all filled tickets that have a live quote.
        # Both paper (to coach simulation habits) and live.
        if last is not None:
            new_actions = await check_and_queue_actions(session, ticket, last)
            for action in new_actions:
                action.notified_at = datetime.now(timezone.utc)
                detail = (
                    f"Move stop: {action.old_stop} → {action.new_stop}"
                    if action.action_type == "trail_stop"
                    else f"Sell {action.sell_shares} sh @ {action.sell_price} ({action.leg_label})"
                )
                alert_trailing_action(
                    ticket.symbol, action.action_type, action.milestone, detail, ticket.is_paper
                )


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
    session: AsyncSession, ticket: Ticket, last: Decimal, broker: BrokerInterface
) -> None:
    """Auto-place scale-out sell orders when exit ladder targets are reached.

    For each unhit, un-queued leg whose price <= last:
    - Places a limit sell via the broker (Day order at the target price).
    - Sets action_queued=True to prevent duplicate orders on subsequent ticks.
    - For paper tickets the broker fills instantly, so we also mark hit=True,
      reduce position_size_shares, and close the ticket if fully exited.
    - For live tickets the fill is detected next tick by _check_scale_out_fills.

    The GTC stop order at the broker is left as-is. On most brokers a stop for
    more shares than the remaining position simply fills for what's available.
    """
    from sqlalchemy.orm.attributes import flag_modified

    plan = ticket.exit_plan
    if not plan or not plan.get("targets"):
        return

    changed = False
    for leg in plan["targets"]:
        if leg.get("hit") or leg.get("action_queued"):
            continue
        leg_price  = Decimal(str(leg["price"]))
        leg_shares = int(leg.get("shares", 0))
        if leg_shares <= 0 or last < leg_price:
            continue

        try:
            order = await place_scale_out_order(
                session, ticket, leg_price, leg_shares, broker
            )
            leg["action_queued"] = True
            changed = True
            log.info("Exit ladder: scale-out queued %s %dsh @ %s (%s)",
                     ticket.symbol, leg_shares, leg_price, leg.get("label", ""))

            # Paper fills instantly — close the leg immediately.
            if order.status == OrderStatus.FILLED.value:
                leg["hit"] = True
                ticket.position_size_shares = max(0, ticket.position_size_shares - leg_shares)
                if ticket.position_size_shares <= 0:
                    await close_ticket(session, ticket, leg_price, "target_hit")

            await log_event(
                session, actor="system", event_type="exit_target_queued",
                entity_type="ticket", entity_id=ticket.id,
                payload={
                    "symbol": ticket.symbol, "label": leg.get("label", ""),
                    "target_price": str(leg_price), "shares": leg_shares,
                    "last_price": str(last), "is_paper": ticket.is_paper,
                },
            )
        except Exception:
            log.exception("Scale-out placement failed for %s leg %s",
                          ticket.symbol, leg.get("label", ""))
            leg["action_queued"] = False  # let next tick retry

    if changed:
        flag_modified(ticket, "exit_plan")


async def _check_scale_out_fills(
    session: AsyncSession, ticket: Ticket, broker: BrokerInterface
) -> None:
    """Poll pending live scale-out orders. On fill: mark leg hit, reduce shares."""
    from sqlalchemy.orm.attributes import flag_modified

    result = await session.execute(
        select(Order).where(
            Order.ticket_id == ticket.id,
            Order.intent == OrderIntent.SCALE_OUT.value,
            Order.status.in_([OrderStatus.SUBMITTED.value, OrderStatus.ACCEPTED.value]),
        )
    )
    pending = result.scalars().all()
    if not pending:
        return

    account = await session.get(Account, ticket.account_id)
    if account is None:
        return

    for order in pending:
        try:
            ack = await broker.get_order(account.questrade_account_id, order.questrade_order_id)
        except Exception:
            log.exception("Failed to poll scale-out order %s", order.questrade_order_id)
            continue

        order.status = ack.status
        if ack.status != "filled":
            continue

        fill_price = ack.fill_price or order.limit_price or ticket.target_price or ticket.trigger_price
        fill_qty   = ack.fill_quantity or order.quantity
        await _record_fill(session, order, fill_price, fill_qty)

        # Match ladder leg by quantity (closest match among action_queued, un-hit legs)
        if ticket.exit_plan and ticket.exit_plan.get("targets"):
            for leg in ticket.exit_plan["targets"]:
                if leg.get("action_queued") and not leg.get("hit") and int(leg.get("shares", 0)) == fill_qty:
                    leg["hit"] = True
                    flag_modified(ticket, "exit_plan")
                    break

        ticket.position_size_shares = max(0, ticket.position_size_shares - fill_qty)
        log.info("Scale-out filled: %s %dsh @ %s, remaining=%d",
                 ticket.symbol, fill_qty, fill_price, ticket.position_size_shares)

        if ticket.position_size_shares <= 0:
            await close_ticket(session, ticket, fill_price, "target_hit")
            return  # ticket closed, stop processing


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
