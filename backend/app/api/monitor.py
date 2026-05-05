from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.settings_service import del_setting, get_setting, set_setting

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

# The MonitorService instance is injected here after it's created in lifespan.
_monitor = None


def set_monitor(m) -> None:
    global _monitor
    _monitor = m


class MonitorStatus(BaseModel):
    running: bool
    armed_tickets: int
    last_tick_at: datetime | None
    kill_switch: bool
    market_open: bool


@router.get("/status", response_model=MonitorStatus)
async def status(session: AsyncSession = Depends(get_session)) -> MonitorStatus:
    from app.services.monitor_service import _is_market_open

    ks_val = await get_setting(session, "kill_switch")
    kill_switch = bool(ks_val and str(ks_val).lower() in ("true", "1", "on"))

    return MonitorStatus(
        running=_monitor.is_running if _monitor else False,
        armed_tickets=_monitor.armed_count if _monitor else 0,
        last_tick_at=_monitor.last_tick_at if _monitor else None,
        kill_switch=kill_switch,
        market_open=_is_market_open(),
    )


@router.post("/kill-switch/enable")
async def enable_kill_switch(session: AsyncSession = Depends(get_session)) -> dict:
    await set_setting(session, "kill_switch", "true")
    await session.commit()
    return {"kill_switch": True}


@router.post("/kill-switch/disable")
async def disable_kill_switch(session: AsyncSession = Depends(get_session)) -> dict:
    await del_setting(session, "kill_switch")
    await session.commit()
    return {"kill_switch": False}


@router.post("/force-check")
async def force_check() -> dict:
    """Dev endpoint: run one full tick immediately, ignoring market hours.
    Useful for testing the trigger→fill→stop cycle outside market hours."""
    if _monitor is None:
        return {"error": "monitor not initialised"}
    from app.db.session import SessionLocal
    from app.services.monitor_service import _is_market_open

    async with SessionLocal() as session:
        armed = await _monitor._load_armed(session)
        if not armed:
            return {"checked": 0, "note": "no armed tickets"}
        await _monitor._evaluate_batch(session, armed)
        await _monitor._expire_stale_tickets(session)
        await session.commit()

    return {"checked": len(armed), "market_open": _is_market_open()}
