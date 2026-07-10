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
                await _snapshot_equity_all_users()
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


async def _snapshot_equity_all_users() -> None:
    """Daily equity snapshot per user from *cached* balances (no broker call).

    Guarantees at most one gap-free row per day even if the user never opens
    the accounts page. Values are only as fresh as the last account sync —
    the source='nightly' tag lets the UI say so.
    """
    from sqlalchemy import select

    from app.db.models import Account
    from app.db.session import SessionLocal
    from app.services.accounts_service import capture_equity_snapshots

    try:
        async with SessionLocal() as session:
            users_q = await session.execute(select(Account.user_id).distinct())
            for (user_id,) in users_q.all():
                await capture_equity_snapshots(session, user_id, source="nightly")
            await session.commit()
    except Exception:
        log.exception("Nightly equity snapshot failed")


_do_sync_lock = asyncio.Lock()


async def _do_sync(symbols: list[str]) -> None:
    """Download incremental EOD bars then immediately rescore the universe.

    Guarded: the nightly loop and the startup stale check can both decide to
    sync within seconds of each other on an evening restart — the second
    trigger is skipped instead of double-downloading and double-scanning.
    """
    from app.db.session import SessionLocal
    from app.services.eod_service import sync_eod_incremental
    from app.services.screener_service import ScanInProgressError, run_screener

    if _do_sync_lock.locked():
        log.info("EOD sync already in progress — skipping duplicate trigger")
        return

    async with _do_sync_lock:
        # Benchmarks ride along: SPY/XIU for the regime model, ZSP.TO for the
        # charter honesty page's CAD counterfactual.
        benchmarks = [s for s in ("SPY", "XIU.TO", "ZSP.TO") if s not in symbols]

        async with SessionLocal() as session:
            counts = await sync_eod_incremental(session, symbols + benchmarks, delta_days=5)
        downloaded = sum(v for v in counts.values() if v > 0)
        log.info("EOD sync: %d/%d symbols updated — running screener rescore", downloaded, len(symbols))

        try:
            async with SessionLocal() as session:
                _, stats = await run_screener(session, mode="auto")
        except ScanInProgressError:
            log.info("Rescore skipped — a manually triggered scan is already running")
            return
        log.info(
            "Nightly rescore complete: %d scored, %d TT-passing, %d with fundamentals, "
            "%d skipped for a stale price feed",
            stats.scored, stats.tt_passing, stats.with_fundamentals, stats.stale_bar_skips,
        )

        await _sync_watchlist_all_users()


async def _sync_watchlist_all_users() -> None:
    """Stage-2 watchlist auto-sync from the fresh Tier S/A picks above.
    Per-user (WatchlistItem is user-scoped), wrapped so one user's failure
    doesn't block others — same resilience pattern as equity snapshots."""
    from sqlalchemy import select

    from app.db.models import Account
    from app.db.session import SessionLocal
    from app.services.watchlist_service import sync_watchlist_from_picks

    try:
        async with SessionLocal() as session:
            users_q = await session.execute(select(Account.user_id).distinct())
            user_ids = [uid for (uid,) in users_q.all()]
            for user_id in user_ids:
                try:
                    await sync_watchlist_from_picks(session, user_id=user_id)
                except Exception:
                    log.exception("Watchlist sync failed for user %s", user_id)
        log.info("Watchlist sync complete for %d users", len(user_ids))
    except Exception:
        log.exception("Watchlist sync loop failed")
