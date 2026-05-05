from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.regime_service import RegimeResult, get_regime

router = APIRouter(prefix="/api/regime", tags=["regime"])


class RegimeOut(BaseModel):
    regime: str
    spy_price: float | None
    spy_ma200: float | None
    spy_pct_vs_ma200: float | None
    xiu_price: float | None
    xiu_ma200: float | None
    xiu_pct_vs_ma200: float | None
    distribution_days: int
    distribution_status: str   # "healthy" | "elevated" | "heavy"
    message: str


@router.get("", response_model=RegimeOut)
async def current_regime(session: AsyncSession = Depends(get_session)) -> RegimeOut:
    r = await get_regime(session)
    return RegimeOut(
        regime=r.regime,
        spy_price=r.spy_price,
        spy_ma200=r.spy_ma200,
        spy_pct_vs_ma200=r.spy_pct_vs_ma200,
        xiu_price=r.xiu_price,
        xiu_ma200=r.xiu_ma200,
        xiu_pct_vs_ma200=r.xiu_pct_vs_ma200,
        distribution_days=r.distribution_days,
        distribution_status=r.distribution_status,
        message=r.message,
    )
