"""Stage-2 pivot watchlist — persisted bridge between Tier S/A screener picks
and armed tickets. See backend/app/db/models.py `WatchlistItem`.

Auto-synced nightly from Tier S/A picks (`screener_service.get_tiered_picks`);
also supports manual add via the API. Purely informational/alerting — this
module never arms a ticket or places an order itself. That stays a human
action; only `MonitorService` (acting on an ARMED `Ticket`) ever touches the
broker. Consistent with the app's behavioral-enforcement philosophy: a human
commits the setup, automation only fires post-commitment.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ScreenerScore,
    Ticket,
    TicketStatus,
    WatchlistItem,
    WatchlistSource,
    WatchlistStatus,
)
from app.services import notifications_service
from app.services.eod_service import get_bars_df
from app.services.screener_service import get_tiered_picks

log = logging.getLogger(__name__)

# Freshness cutoff for the ScreenerScore a watchlist item is compared against
# — matches the cutoff Tier S/A/B eligibility uses (screener_service.py).
MAX_SCORE_AGE_DAYS = 5

# "Approaching" band: an in_base row whose extension sits inside this range,
# with volume already picking up, gets an early heads-up alert. Deliberately
# a lower volume bar than MonitorService's 1.5x breakout-confirm threshold —
# this is "start paying attention," not a trigger. -3.0 is the lower edge of
# pattern_service's own at_pivot band, so this picks up right where "in_base"
# ends and "at_pivot" begins.
NEAR_PIVOT_LOW_PCT = -8.0
NEAR_PIVOT_HIGH_PCT = -3.0
NEAR_PIVOT_VOLUME_MULTIPLE = 1.2

_ALERT_STATUSES = (WatchlistStatus.NEAR_PIVOT, WatchlistStatus.AT_PIVOT)
_AUTO_SOURCE_BY_TIER = {"S": WatchlistSource.TIER_S, "A": WatchlistSource.TIER_A}


async def _latest_fresh_score(session: AsyncSession, symbol: str) -> ScreenerScore | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_SCORE_AGE_DAYS)
    result = await session.execute(select(ScreenerScore).where(ScreenerScore.symbol == symbol))
    score = result.scalar_one_or_none()
    if score is None or score.scored_at is None or score.scored_at < cutoff:
        return None
    return score


async def _volume_ratio(session: AsyncSession, symbol: str) -> float | None:
    """Latest daily volume / mean of the prior 20 days. None if there aren't
    enough bars to be conclusive — a data gap should never trigger a false
    near-pivot alert."""
    df = await get_bars_df(session, symbol, days=21)
    if len(df) < 21:
        return None
    volumes = df["volume"].to_numpy()
    latest = float(volumes[-1])
    prior_avg = float(volumes[:-1].mean())
    if prior_avg <= 0:
        return None
    return latest / prior_avg


def _derive_status(score: ScreenerScore, volume_ratio: float | None) -> WatchlistStatus | None:
    """Pure function: current ScreenerScore + volume signal -> new status.

    Returns None when the data is inconclusive and the caller should leave
    the item's status unchanged — never flip a row to BROKEN just because a
    signal is temporarily missing.
    """
    buyability = score.buyability
    if buyability in (None, "frozen"):
        return None
    if buyability in ("broken", "no_pattern"):
        return WatchlistStatus.BROKEN
    if buyability == "extended":
        return WatchlistStatus.EXTENDED
    if buyability == "at_pivot":
        return WatchlistStatus.AT_PIVOT
    if buyability == "in_base":
        ext = float(score.extension_pct) if score.extension_pct is not None else None
        if (
            ext is not None
            and NEAR_PIVOT_LOW_PCT <= ext < NEAR_PIVOT_HIGH_PCT
            and volume_ratio is not None
            and volume_ratio >= NEAR_PIVOT_VOLUME_MULTIPLE
        ):
            return WatchlistStatus.NEAR_PIVOT
        return WatchlistStatus.WATCHING
    return None


def _fire_alert(symbol: str, score: ScreenerScore, new_status: WatchlistStatus) -> None:
    last_close = score.last_close or Decimal(0)
    pivot = score.pivot_price or Decimal(0)
    if new_status == WatchlistStatus.AT_PIVOT:
        notifications_service.alert_watchlist_at_pivot(symbol, last_close, pivot)
    else:
        ext = float(score.extension_pct) if score.extension_pct is not None else 0.0
        notifications_service.alert_watchlist_near_pivot(symbol, last_close, pivot, ext)


async def _unarm_if_ticket_dead(session: AsyncSession, item: WatchlistItem) -> None:
    """An armed item whose ticket got cancelled/expired shouldn't stay frozen
    forever — un-arm it so it re-enters the normal alerting flow."""
    if item.ticket_id is None:
        return
    ticket = await session.get(Ticket, item.ticket_id)
    if ticket is None or ticket.status in (TicketStatus.CANCELLED.value, TicketStatus.EXPIRED.value):
        item.ticket_id = None
        item.status = WatchlistStatus.WATCHING.value
        item.status_changed_at = datetime.now(timezone.utc)
        item.last_notified_status = None


async def check_pivot_proximity(session: AsyncSession, user_id: str) -> None:
    """Re-evaluate every active (non-removed) watchlist item for this user
    against the latest fresh ScreenerScore, updating status and firing at
    most one alert per distinct transition into NEAR_PIVOT/AT_PIVOT."""
    result = await session.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id,
            WatchlistItem.status != WatchlistStatus.REMOVED.value,
        )
    )
    items = result.scalars().all()

    for item in items:
        if item.status == WatchlistStatus.ARMED.value:
            await _unarm_if_ticket_dead(session, item)
            continue

        score = await _latest_fresh_score(session, item.symbol)
        if score is None:
            continue

        vol_ratio = await _volume_ratio(session, item.symbol)
        new_status = _derive_status(score, vol_ratio)
        if new_status is None or new_status.value == item.status:
            continue

        item.status = new_status.value
        item.status_changed_at = datetime.now(timezone.utc)

        if new_status in _ALERT_STATUSES:
            if item.last_notified_status != new_status.value:
                _fire_alert(item.symbol, score, new_status)
                item.last_notified_status = new_status.value
        else:
            # Leaving the alert zone re-arms the alert for a future re-approach.
            item.last_notified_status = None

    await session.commit()


async def sync_watchlist_from_picks(session: AsyncSession, user_id: str) -> None:
    """Nightly: add newly-qualifying Tier S/A picks, soft-remove items whose
    symbol has fallen out of the scan universe entirely, then re-evaluate
    every active item's proximity status.

    Idempotent — safe to re-run against the same nightly snapshot without
    duplicating rows or re-firing alerts.
    """
    tiers = await get_tiered_picks(session, max_age_days=MAX_SCORE_AGE_DAYS)
    candidates = tiers["S"] + tiers["A"]
    candidate_tier: dict[str, str] = {r.symbol: "S" for r in tiers["S"]}
    candidate_tier.update({r.symbol: "A" for r in tiers["A"]})

    result = await session.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id,
            WatchlistItem.status != WatchlistStatus.REMOVED.value,
        )
    )
    active_items = result.scalars().all()
    active_symbols = {i.symbol for i in active_items}

    now = datetime.now(timezone.utc)
    for score in candidates:
        if score.symbol in active_symbols:
            continue  # already tracked — check_pivot_proximity below updates it
        if score.pivot_price is None:
            continue  # nothing to lock in
        tier = candidate_tier[score.symbol]
        session.add(WatchlistItem(
            user_id=user_id,
            symbol=score.symbol,
            pivot_price=score.pivot_price,
            source=_AUTO_SOURCE_BY_TIER[tier].value,
            pattern_type=score.pattern_type,
            status=WatchlistStatus.AT_PIVOT.value,  # S/A both require at_pivot to qualify
            added_at=now,
            status_changed_at=now,
        ))

    # Soft-remove active items whose symbol has no fresh score at all — distinct
    # from BROKEN, which means "we have a current opinion and it's bad."
    for item in active_items:
        if item.status == WatchlistStatus.ARMED.value:
            continue
        score = await _latest_fresh_score(session, item.symbol)
        if score is None:
            item.status = WatchlistStatus.REMOVED.value
            item.status_changed_at = now

    await session.flush()
    await check_pivot_proximity(session, user_id)


async def link_ticket_to_watchlist_item(
    session: AsyncSession,
    *,
    watchlist_item_id: uuid.UUID | None,
    ticket: Ticket,
    user_id: str,
) -> None:
    """Best-effort: mark a watchlist item ARMED once its ticket is created.

    Never raises — a bad or foreign watchlist_item_id must not block ticket
    creation, since the ticket itself is the load-bearing artifact.
    """
    if watchlist_item_id is None:
        return
    try:
        item = await session.get(WatchlistItem, watchlist_item_id)
        if item is None or item.user_id != user_id or item.symbol != ticket.symbol:
            log.warning(
                "watchlist_item_id %s not linkable to ticket %s (symbol=%s, user=%s)",
                watchlist_item_id, ticket.id, ticket.symbol, user_id,
            )
            return
        item.ticket_id = ticket.id
        item.status = WatchlistStatus.ARMED.value
        item.status_changed_at = datetime.now(timezone.utc)
        item.last_notified_status = None
        await session.flush()
    except Exception:
        log.exception(
            "Failed to link ticket %s to watchlist item %s", ticket.id, watchlist_item_id,
        )
