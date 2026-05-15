"""Breakout monitor — polls quotes for armed tickets, fires triggers, places orders.

State machine per ticket:
  armed ──(trigger)──► triggered ──(entry fill)──► filled
                                          └── stop order (GTC) armed

Lifecycle also handles:
  armed ──(expires_at passed)──► expired

Volume confirmation (price_above_with_volume): projects current intraday volume
to end-of-day and requires it to exceed ADV × ticket.volume_confirm_multiple.
Real-money tickets with delayed quotes (delay > 0) are skipped.
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
from app.db.models import DailyBar, EarningsDate, Ticket, TicketStatus, TriggerType
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

        # Load 50-day ADV for any volume-confirm tickets.
        vol_symbols = [t.symbol for t in tickets
                       if t.trigger_type == TriggerType.PRICE_ABOVE_WITH_VOLUME.value]
        avg_vol_map = await self._load_adv(session, vol_symbols) if vol_symbols else {}

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
            avg_vol = avg_vol_map.get(ticket.symbol)
            if self._is_triggered(ticket, quote, avg_vol=avg_vol):
                await self._fire_trigger(session, ticket, quote)

        # Check filled tickets for stop/target hits.
        await check_filled_tickets_for_close(session, self._broker, quotes)

    async def _load_adv(self, session: AsyncSession, symbols: list[str]) -> dict[str, int]:
        """50-day average daily volume. Primary: EarningsDate.avg_volume.
        Fallback: compute from the last 50 daily_bars rows."""
        result: dict[str, int] = {}

        # Primary: already-computed field on EarningsDate
        ed_result = await session.execute(
            select(EarningsDate.symbol, EarningsDate.avg_volume)
            .where(EarningsDate.symbol.in_(symbols))
            .where(EarningsDate.avg_volume.isnot(None))
        )
        for row in ed_result:
            result[row.symbol] = int(row.avg_volume)

        # Fallback: compute from daily_bars for any missing symbol
        missing = [s for s in symbols if s not in result]
        for sym in missing:
            bars = await session.execute(
                select(DailyBar.volume)
                .where(DailyBar.symbol == sym, DailyBar.volume > 0)
                .order_by(DailyBar.bar_date.desc())
                .limit(50)
            )
            vols = [r.volume for r in bars]
            if vols:
                result[sym] = int(sum(vols) / len(vols))

        return result

    def _is_triggered(self, ticket: Ticket, quote: BrokerQuote, *, avg_vol: int | None = None) -> bool:
        last    = quote.last
        trigger = Decimal(str(ticket.trigger_price))

        if last < trigger:
            return False  # Price condition never met — fast path

        if ticket.trigger_type == TriggerType.PRICE_ABOVE.value:
            return True

        if ticket.trigger_type == TriggerType.PRICE_ABOVE_WITH_VOLUME.value:
            return self._volume_confirms(ticket, quote, avg_vol)

        if ticket.trigger_type == TriggerType.DAY_CLOSE_ABOVE.value:
            # Handled separately in the post-close window; should not appear here
            return True

        return False

    def _volume_confirms(self, ticket: Ticket, quote: BrokerQuote, avg_vol: int | None) -> bool:
        """True when projected end-of-day volume clears the ticket's multiple of ADV.

        Projection: current_volume / time_fraction_of_session.
        The first minute is skipped (opening-auction data is unreliable).
        For 1–15 min: only confirm if current volume ALREADY exceeds the full
        requirement — this catches massive gap-up events without relying on
        noisy early projections.
        After 15 min: use the projection so the trade fires at a reasonable time
        on a legitimate breakout rather than waiting until afternoon.

        If ADV is unavailable, we confirm rather than block — missing data
        should never silently prevent a trade from firing.
        """
        if avg_vol is None or avg_vol <= 0:
            log.warning("%s: no ADV data — volume check skipped, treating as confirmed", ticket.symbol)
            return True

        current_vol = quote.volume
        if not current_vol or current_vol <= 0:
            log.debug("%s: no intraday volume in quote — not yet confirmed", ticket.symbol)
            return False

        multiple = float(ticket.volume_confirm_multiple or 1.5)
        required = avg_vol * multiple

        now_et = _eastern_now()
        market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_elapsed = max(0.0, (now_et - market_open).total_seconds() / 60.0)

        if minutes_elapsed < 1:
            # Opening auction — quote volumes are meaningless
            log.debug("%s: opening auction, skipping volume check", ticket.symbol)
            return False

        if minutes_elapsed < 15:
            # Too early to project reliably. Only fire if current volume already
            # clears the full-day requirement (e.g. monster earnings gap-up).
            confirms = current_vol >= required
            log.info(
                "%s early-session volume: elapsed=%.0fmin current=%d required=%.0f "
                "(ADV=%d × %.1f) → %s",
                ticket.symbol, minutes_elapsed, current_vol, required, avg_vol, multiple,
                "CONFIRM" if confirms else "waiting (< 15min, no projection yet)",
            )
            return confirms

        # 15+ minutes in: project to end of day
        _SESSION_MINUTES = 390.0  # 9:30–16:00 ET
        time_fraction = min(minutes_elapsed / _SESSION_MINUTES, 1.0)
        projected = int(current_vol / time_fraction)
        confirms = projected >= required

        log.info(
            "%s volume: elapsed=%.0fmin current=%d projected=%d required=%.0f "
            "(ADV=%d × %.1f) → %s",
            ticket.symbol, minutes_elapsed, current_vol, projected, required, avg_vol, multiple,
            "CONFIRM" if confirms else "reject",
        )
        return confirms

    async def _fire_trigger(
        self, session: AsyncSession, ticket: Ticket, quote: BrokerQuote
    ) -> None:
        """Transition armed→triggered, place entry order, send alert."""
        ticket.status = TicketStatus.TRIGGERED.value
        ticket.triggered_at = datetime.now(timezone.utc)

        log.info("TRIGGER: %s last=%s trigger=%s type=%s",
                 ticket.symbol, quote.last, ticket.trigger_price, ticket.trigger_type)

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
