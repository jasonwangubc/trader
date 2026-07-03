"""Trading charter API: append-only pre-commitment versions + the honesty
page (actual equity vs deposit-timing-matched benchmark counterfactual)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_id
from app.db.models import CharterVersion
from app.db.session import get_session
from app.services.charter_service import (
    compute_performance,
    create_charter_version,
    get_active_charter,
    list_charter_versions,
)

router = APIRouter(prefix="/api/charter", tags=["charter"])


class CharterOut(BaseModel):
    id: str
    version: int
    content_md: str
    rules: dict
    note: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: CharterVersion) -> CharterOut:
        return cls(
            id=str(row.id),
            version=row.version,
            content_md=row.content_md,
            rules=row.rules or {},
            note=row.note,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )


class CharterIn(BaseModel):
    content_md: str = Field(min_length=50, max_length=50_000)
    rules: dict | None = None
    note: str | None = Field(default=None, max_length=500)


@router.get("", response_model=CharterOut | None)
async def active_charter(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> CharterOut | None:
    row = await get_active_charter(session, user_id)
    return CharterOut.from_row(row) if row else None


@router.get("/versions", response_model=list[CharterOut])
async def versions(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[CharterOut]:
    rows = await list_charter_versions(session, user_id)
    return [CharterOut.from_row(r) for r in rows]


@router.post("", response_model=CharterOut, status_code=201)
async def create_version(
    body: CharterIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> CharterOut:
    """Publish a new charter version. There is intentionally no PUT/DELETE —
    the pre-commitment property lives in immutable, audited versions."""
    if body.note is None:
        current = await get_active_charter(session, user_id)
        if current is not None:
            raise HTTPException(
                status_code=422,
                detail="Revising the charter requires a note explaining why.",
            )
    row = await create_charter_version(
        session,
        user_id=user_id,
        content_md=body.content_md,
        rules=body.rules,
        note=body.note,
    )
    return CharterOut.from_row(row)


class PerfPointOut(BaseModel):
    date: str
    counterfactual: float | None
    actual: float | None


class MonthRowOut(BaseModel):
    month: str
    actual_end: float | None
    counterfactual_end: float | None


class CurrencyPerformanceOut(BaseModel):
    currency: str
    benchmark_symbol: str
    deposits_total: float
    withdrawals_total: float
    flow_count: int
    points: list[PerfPointOut]
    monthly: list[MonthRowOut]
    actual_max_drawdown_pct: float | None
    latest_actual: float | None
    latest_counterfactual: float | None
    status: str
    status_detail: str


class PerformanceOut(BaseModel):
    currencies: list[CurrencyPerformanceOut]


@router.get("/performance", response_model=PerformanceOut)
async def performance(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> PerformanceOut:
    """Actual equity vs 'what if I had indexed the same deposits' — the
    honest benchmark that the charter's kill/scale criteria evaluate against."""
    results = await compute_performance(session, user_id)
    return PerformanceOut(currencies=[
        CurrencyPerformanceOut(
            currency=r.currency,
            benchmark_symbol=r.benchmark_symbol,
            deposits_total=r.deposits_total,
            withdrawals_total=r.withdrawals_total,
            flow_count=r.flow_count,
            points=[PerfPointOut(**p.__dict__) for p in r.points],
            monthly=[MonthRowOut(**m.__dict__) for m in r.monthly],
            actual_max_drawdown_pct=r.actual_max_drawdown_pct,
            latest_actual=r.latest_actual,
            latest_counterfactual=r.latest_counterfactual,
            status=r.status,
            status_detail=r.status_detail,
        )
        for r in results
    ])
