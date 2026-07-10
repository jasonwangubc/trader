"""Stage-2 pivot watchlist API.

NOT the same thing as /api/screener/watchlist (ScreenerSymbol — the nightly
scan universe). This router manages WatchlistItem: Tier S/A picks (auto-added
nightly) or manually-added symbols, tracked with a pivot price locked at add
time. See app/services/watchlist_service.py and app/db/models.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_id
from app.db.models import ScreenerScore, WatchlistItem, WatchlistSource, WatchlistStatus
from app.db.session import get_session

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistItemOut(BaseModel):
    id: uuid.UUID
    symbol: str
    sector: str | None
    pivot_price: Decimal          # locked at add time
    source: str
    pattern_type: str | None
    status: str
    added_at: datetime
    status_changed_at: datetime
    ticket_id: uuid.UUID | None
    notes: str | None
    # Live fields, joined from the latest ScreenerScore (may be None if the
    # symbol has no fresh score) — the item's own pivot_price above stays the
    # locked value even if the score's pivot has since drifted on a rescan.
    last_close: Decimal | None = None
    extension_pct: Decimal | None = None
    buyability: str | None = None
    composite_score: float | None = None


class WatchlistAddIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    pivot_price: Decimal | None = None


@router.get("", response_model=list[WatchlistItemOut])
async def list_watchlist(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[WatchlistItemOut]:
    result = await session.execute(
        select(WatchlistItem)
        .where(WatchlistItem.user_id == user_id)
        .where(WatchlistItem.status != WatchlistStatus.REMOVED.value)
        .order_by(WatchlistItem.status_changed_at.desc())
    )
    items = result.scalars().all()
    if not items:
        return []

    symbols = {i.symbol for i in items}
    score_result = await session.execute(
        select(ScreenerScore).where(ScreenerScore.symbol.in_(symbols))
    )
    score_by_symbol = {s.symbol: s for s in score_result.scalars().all()}

    out: list[WatchlistItemOut] = []
    for item in items:
        score = score_by_symbol.get(item.symbol)
        out.append(WatchlistItemOut(
            id=item.id,
            symbol=item.symbol,
            sector=score.sector if score else None,
            pivot_price=item.pivot_price,
            source=item.source,
            pattern_type=item.pattern_type,
            status=item.status,
            added_at=item.added_at,
            status_changed_at=item.status_changed_at,
            ticket_id=item.ticket_id,
            notes=item.notes,
            last_close=score.last_close if score else None,
            extension_pct=score.extension_pct if score else None,
            buyability=score.buyability if score else None,
            composite_score=round(float(score.composite_score) * 100, 1) if score else None,
        ))
    return out


@router.post("", response_model=WatchlistItemOut, status_code=201)
async def add_watchlist_item(
    body: WatchlistAddIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> WatchlistItemOut:
    symbol = body.symbol.strip().upper()

    existing = await session.execute(
        select(WatchlistItem).where(
            WatchlistItem.user_id == user_id,
            WatchlistItem.symbol == symbol,
            WatchlistItem.status != WatchlistStatus.REMOVED.value,
        )
    )
    dupe = existing.scalar_one_or_none()
    if dupe is not None:
        raise HTTPException(
            status_code=409,
            detail={"code": "already_watching", "watchlist_item_id": str(dupe.id)},
        )

    score_result = await session.execute(select(ScreenerScore).where(ScreenerScore.symbol == symbol))
    score = score_result.scalar_one_or_none()

    pivot = body.pivot_price if body.pivot_price is not None else (score.pivot_price if score else None)
    if pivot is None:
        raise HTTPException(
            status_code=422,
            detail="No pivot price known for this symbol — supply pivot_price explicitly.",
        )

    now = datetime.now(timezone.utc)
    status = WatchlistStatus.WATCHING.value
    if score and score.buyability == "at_pivot":
        status = WatchlistStatus.AT_PIVOT.value

    item = WatchlistItem(
        user_id=user_id,
        symbol=symbol,
        pivot_price=pivot,
        source=WatchlistSource.MANUAL.value,
        pattern_type=score.pattern_type if score else None,
        status=status,
        added_at=now,
        status_changed_at=now,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    return WatchlistItemOut(
        id=item.id,
        symbol=item.symbol,
        sector=score.sector if score else None,
        pivot_price=item.pivot_price,
        source=item.source,
        pattern_type=item.pattern_type,
        status=item.status,
        added_at=item.added_at,
        status_changed_at=item.status_changed_at,
        ticket_id=item.ticket_id,
        notes=item.notes,
        last_close=score.last_close if score else None,
        extension_pct=score.extension_pct if score else None,
        buyability=score.buyability if score else None,
        composite_score=round(float(score.composite_score) * 100, 1) if score else None,
    )


@router.delete("/{item_id}", status_code=204)
async def remove_watchlist_item(
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> None:
    item = await session.get(WatchlistItem, item_id)
    if item is None or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Watchlist item not found")

    item.status = WatchlistStatus.REMOVED.value
    item.status_changed_at = datetime.now(timezone.utc)
    await session.commit()
