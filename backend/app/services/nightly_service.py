"""Nightly EOD data refresh — runs automatically after market close.

Schedule: weekdays at 17:30 ET (1.5h after NYSE/TSX close).
Also triggered on app startup if bars are more than one trading day stale.

The 1.5-hour delay is deliberate: yfinance occasionally serves a 404 or stale
data for the current day in the 30-60 minutes immediately after 4 PM ET.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from dateutil import tz

log = logging.getLogger(__name__)

_ET = tz.gettz("America/New_York")
_SYNC_HOUR   = 17   # 5 PM ET
_SYNC_MINUTE = 30   # :30 → 5:30 PM ET


def _et_now() -> datetime:
    return datetime.now(tz=_ET)


def _last_trading_day(ref: datetime) -> date:
    """Return the most recent completed trading day as of `ref` (Eastern time)."""
    d = ref.date()
    # If before today's close (or weekend), step back
    if ref.weekday() >= 5:
        # Weekend: go to Friday
        days_back = ref.weekday() - 4
        d = d - timedelta(days=days_back)
    elif ref.hour < _SYNC_HOUR or (ref.hour == _SYNC_HOUR and ref.minute < _SYNC_MINUTE):
        # Before today's sync window — previous weekday is most recent complete day
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d


def _is_stale(latest_bar_date: date) -> bool:
    """True if latest bar is before the most recently completed trading day."""
    expected = _last_trading_day(_et_now())
    return latest_bar_date < expected


async def run_nightly_loop() -> None:
    """Background task: syncs EOD data once per trading day after 5:30 PM ET."""
    from app.db.session import SessionLocal
    from app.services.eod_service import sync_eod_incremental
    from app.db.models import ScreenerSymbol
    from sqlalchemy import select

    last_synced: date | None = None
    log.info("Nightly EOD scheduler started (fires at 17:30 ET on weekdays)")

    while True:
        try:
            now = _et_now()
            today = now.date()
            is_weekday     = now.weekday() < 5
            past_sync_time = now.hour > _SYNC_HOUR or (now.hour == _SYNC_HOUR and now.minute >= _SYNC_MINUTE)

            if is_weekday and past_sync_time and last_synced != today:
                log.info("Nightly EOD sync starting (%s)", today)
                async with SessionLocal() as session:
                    sym_result = await session.execute(
                        select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
                    )
                    symbols = [r for (r,) in sym_result.all()]
                await _do_sync(symbols)
                last_synced = today
                log.info("Nightly EOD sync complete for %s (%d symbols)", today, len(symbols))

        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Nightly EOD sync loop error")

        await asyncio.sleep(1800)  # check every 30 minutes


async def startup_stale_check() -> None:
    """Called at startup: if data is stale, trigger a sync immediately."""
    await asyncio.sleep(20)  # let the app fully initialise first
    try:
        from app.db.session import SessionLocal
        from app.db.models import DailyBar, ScreenerSymbol
        from sqlalchemy import func, select, text

        async with SessionLocal() as session:
            result = await session.execute(
                select(func.max(DailyBar.bar_date))
            )
            latest = result.scalar_one_or_none()

        if latest is None:
            log.info("Startup check: no bars found — skipping auto-sync (run a scan first)")
            return

        latest_date = latest.date() if hasattr(latest, "date") else latest
        if _is_stale(latest_date):
            expected = _last_trading_day(_et_now())
            log.info(
                "Startup check: bars stale (latest=%s, expected=%s) — triggering EOD refresh",
                latest_date, expected,
            )
            from app.db.session import SessionLocal as SL
            from app.db.models import ScreenerSymbol
            async with SL() as session:
                sym_result = await session.execute(
                    select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
                )
                symbols = [r for (r,) in sym_result.all()]
            if symbols:
                await _do_sync(symbols)
                log.info("Startup stale-data sync complete (%d symbols)", len(symbols))
        else:
            log.info("Startup check: data is current (latest bar=%s)", latest_date)

    except Exception:
        log.exception("Startup stale-check failed")


async def _do_sync(symbols: list[str]) -> None:
    """Run the incremental EOD download for the given symbols."""
    from app.db.session import SessionLocal
    from app.services.eod_service import sync_eod_incremental

    async with SessionLocal() as session:
        counts = await sync_eod_incremental(session, symbols, delta_days=5)
    downloaded = sum(v for v in counts.values() if v > 0)
    log.info("EOD sync: %d/%d symbols updated", downloaded, len(symbols))
