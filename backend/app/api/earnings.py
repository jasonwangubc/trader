"""Earnings calendar API."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EarningsDate
from app.db.session import get_session
from app.services.earnings_service import (
    days_to_earnings,
    earnings_warning,
    sync_earnings,
)

router = APIRouter(prefix="/api/earnings", tags=["earnings"])

_sync_running = False


class EarningsOut(BaseModel):
    symbol: str
    next_earnings_date: datetime | None
    days_until: int | None
    last_earnings_date: datetime | None
    last_eps_surprise_pct: Decimal | None
    warning: str | None
    synced_at: datetime


@router.get("", response_model=list[EarningsOut])
async def list_earnings(session: AsyncSession = Depends(get_session)) -> list[EarningsOut]:
    result = await session.execute(
        select(EarningsDate).order_by(EarningsDate.next_earnings_date.asc().nullslast())
    )
    rows = result.scalars().all()
    return [_to_out(r) for r in rows]


@router.get("/{symbol}", response_model=EarningsOut)
async def get_earnings(symbol: str, session: AsyncSession = Depends(get_session)) -> EarningsOut:
    result = await session.execute(
        select(EarningsDate).where(EarningsDate.symbol == symbol.upper())
    )
    row = result.scalar_one_or_none()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(404, f"No earnings data for {symbol}. Run /api/earnings/sync first.")
    return _to_out(row)


@router.post("/sync")
async def trigger_sync(
    background_tasks: BackgroundTasks,
    symbols: list[str] | None = None,
) -> dict:
    global _sync_running
    if _sync_running:
        return {"running": True, "message": "Sync already in progress"}
    _sync_running = True
    background_tasks.add_task(_run_sync, symbols)
    return {"running": True, "message": "Earnings sync started"}


async def _run_sync(symbols: list[str] | None) -> None:
    global _sync_running
    from app.db.session import SessionLocal
    try:
        async with SessionLocal() as session:
            await sync_earnings(session, symbols)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Earnings sync failed")
    finally:
        _sync_running = False


def _to_out(r: EarningsDate) -> EarningsOut:
    return EarningsOut(
        symbol=r.symbol,
        next_earnings_date=r.next_earnings_date,
        days_until=days_to_earnings(r),
        last_earnings_date=r.last_earnings_date,
        last_eps_surprise_pct=r.last_eps_surprise_pct,
        warning=earnings_warning(r),
        synced_at=r.synced_at,
    )
