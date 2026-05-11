"""Breakout monitor — polls quotes for armed tickets, fires triggers, places orders.

State machine per ticket:
  armed ──(trigger)──► triggered ──(entry fill)──► filled
                                          └── stop order (GTC) armed

Lifecycle also handles:
  armed ──(expires_at passed)──► expired

Volume confirmation (price_above_with_volume) requires historical average volume
from yfinance, which arrives in Sprint 3. Price-only check is used for now with
an audit note. Real-money tickets with delayed quotes (delay > 0) are skipped.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

from dateutil import tz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface, BrokerQuote
from app.db.models import Ticket, TicketStatus, TriggerType
from app.db.session import SessionLocal
from app.services.audit_service import log_event
from app.services.notifications_service import alert_filled, alert_stopped_out, alert_target_hit, alert_triggered
from app.services.order_service import (
    check_filled_tickets_for_close,
    check_open_entry_orders,
    on_entry_filled,
    place_entry_order,
)
from app.services.settings_service import get_setting

log = logging.getLogger(__name__)

POLL_INTERVAL_SECS = 15
_ET = tz.gettz("America/New_York")
_CLOSE_EVAL_WINDOW_MINS = 10


def _eastern_now() -> datetime:
    return datetime.now(tz=_ET)


def _is_market_open() -> bool:
    now = _eastern_now()
    if now.weekday() >= 5:
        return False
    from datetime import time
    return time(9, 30) <= now.time() < time(16, 0)


def _is_post_close_window() -> bool:
    now = _eastern_now()
    if now.weekday() >= 5:
        return False
    from datetime import time
    return time(16, 0) <= now.time() < time(16, _CLOSE_EVAL_WINDOW_MINS)


class MonitorService:
    def __init__(self, broker: BrokerInterface) -> None:
        self._broker = broker
        self._running = False
        self._last_tick_at: datetime | None = None
        self._last_close_check_date: date | None = None
        self._armed_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    @property
    def armed_count(self) -> int:
        return self._armed_count

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        log.info("Breakout monitor started (poll every %ds)", POLL_INTERVAL_SECS)
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Monitor tick error")
            await asyncio.sleep(POLL_INTERVAL_SECS)
        log.info("Breakout monitor stopped")

    async def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        market_open = _is_market_open()
        post_close = _is_post_close_window()

        async with SessionLocal() as session:
            if await self._kill_switch_active(session):
                return

            # Expire stale tickets every tick (regardless of market hours).
            await self._expire_stale_tickets(session)

            # Poll live entry orders for fills.
            if market_open or post_close:
                await check_open_entry_orders(session, self._broker)

            armed = await self._load_armed(session)
            self._armed_count = len(armed)

            if armed and (market_open or post_close):
                intraday = [t for t in armed if t.trigger_type != TriggerType.DAY_CLOSE_ABOVE.value]
                at_close = [t for t in armed if t.trigger_type == TriggerType.DAY_CLOSE_ABOVE.value]

                if market_open and intraday:
                    await self._evaluate_batch(session, intraday)

                today = _eastern_now().date()
                if post_close and at_close and self._last_close_check_date != today:
                    await self._evaluate_batch(session, at_close)
                    self._last_close_check_date = today

            await session.commit()

        self._last_tick_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _kill_switch_active(self, session: AsyncSession) -> bool:
        val = await get_setting(session, "kill_switch")
        return bool(val and str(val).lower() in ("true", "1", "on"))

    async def _load_armed(self, session: AsyncSession) -> list[Ticket]:
        r = await session.execute(
            select(Ticket).where(Ticket.status == TicketStatus.ARMED.value)
        )
        return list(r.scalars().all())

    async def _expire_stale_tickets(self, session: AsyncSession) -> None:
        now = datetime.now(timezone.utc)
        r = await session.execute(
            select(Ticket).where(
                Ticket.status == TicketStatus.ARMED.value,
                Ticket.expires_at != None,  # noqa: E711
                Ticket.expires_at <= now,
            )
        )
        for ticket in r.scalars().all():
            ticket.status = TicketStatus.EXPIRED.value
            await log_event(
                session,
                actor="system",
                event_type="ticket_expired",
                entity_type="ticket",
                entity_id=ticket.id,
                payload={"symbol": ticket.symbol, "expires_at": str(ticket.expires_at)},
            )
            log.info("Ticket expired: %s (%s)", ticket.symbol, ticket.id)

    async def _evaluate_batch(self, session: AsyncSession, tickets: list[Ticket]) -> None:
        # Include filled-ticket symbols so we can check stop/target hits.
        filled_result = await session.execute(
            select(Ticket).where(Ticket.status == TicketStatus.FILLED.value)
        )
        filled_tickets = filled_result.scalars().all()

        all_symbols = list({t.symbol for t in tickets} | {t.symbol for t in filled_tickets})
        try:
            quotes = await self._broker.get_quotes_batch(all_symbols)
        except Exception:
            log.exception("Quote fetch failed; skipping tick")
            return

        # Check armed tickets for triggers.
        for ticket in tickets:
            quote = quotes.get(ticket.symbol)
            if quote is None:
                log.warning("No quote for %s — skipping", ticket.symbol)
                continue
            if quote.delay > 0 and not ticket.is_paper:
                log.warning("%s quote delayed %dmin — skipping real-money check",
                            ticket.symbol, quote.delay)
                continue
            if self._is_triggered(ticket, quote):
                await self._fire_trigger(session, ticket, quote)

        # Check filled tickets for stop/target hits.
        await check_filled_tickets_for_close(session, self._broker, quotes)

    def _is_triggered(self, ticket: Ticket, quote: BrokerQuote) -> bool:
        last = quote.last
        trigger = Decimal(str(ticket.trigger_price))
        if ticket.trigger_type in (
            TriggerType.PRICE_ABOVE.value,
            TriggerType.PRICE_ABOVE_WITH_VOLUME.value,
            TriggerType.DAY_CLOSE_ABOVE.value,
        ):
            return last >= trigger
        return False

    async def _fire_trigger(
        self, session: AsyncSession, ticket: Ticket, quote: BrokerQuote
    ) -> None:
        """Transition armed→triggered, place entry order, send alert."""
        ticket.status = TicketStatus.TRIGGERED.value
        ticket.triggered_at = datetime.now(timezone.utc)

        volume_note = (
            " (volume confirmation deferred to Sprint 3)"
            if ticket.trigger_type == TriggerType.PRICE_ABOVE_WITH_VOLUME.value
            else ""
        )
        log.info("TRIGGER: %s last=%s trigger=%s%s", ticket.symbol, quote.last,
                 ticket.trigger_price, volume_note)

        await log_event(
            session,
            actor="system",
            event_type="ticket_triggered",
            entity_type="ticket",
            entity_id=ticket.id,
            payload={
                "symbol": ticket.symbol,
                "trigger_type": ticket.trigger_type,
                "trigger_price": str(ticket.trigger_price),
                "last_price": str(quote.last),
                "volume": quote.volume,
                "quote_delay_mins": quote.delay,
                "is_paper": ticket.is_paper,
                "note": volume_note.strip() or None,
            },
        )

        # Place entry order.
        try:
            entry_order = await place_entry_order(session, ticket, self._broker, quote.last)
            alert_triggered(ticket.symbol, ticket.trigger_price, quote.last,
                            ticket.is_paper, ticket.position_size_shares)

            # Paper broker fills instantly — complete the fill cycle now.
            if entry_order.status == "filled" or (
                entry_order.questrade_order_id
                and entry_order.questrade_order_id.startswith("paper-")
            ):
                fill_price = quote.last
                await on_entry_filled(session, ticket, entry_order, fill_price, self._broker)
                alert_filled(ticket.symbol, fill_price, ticket.stop_price,
                             ticket.position_size_shares, ticket.is_paper)
                # Exit plan is now auto-set in on_entry_filled for all fills.

        except Exception:
            log.exception("Order placement failed for %s — ticket stays triggered", ticket.symbol)
