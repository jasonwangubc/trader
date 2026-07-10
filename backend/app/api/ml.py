"""ML setup-ranker API — train the model, inspect it, backfill 5y bars.

Follows the backtest API's long-job convention: BackgroundTasks + in-process
singleton flags + status polling. Single-worker assumption (same as backtest).
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MLModel
from app.db.session import get_session
from app.services.ml_ranker import MLTrainProgress, ml_available, train_ranker

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml", tags=["ml"])

_training = False
_train_progress = MLTrainProgress()
_train_error: str | None = None
_last_model_id: str | None = None

_backfill_running = False
_backfill_done_symbols = 0
_backfill_total_symbols = 0
_backfill_error: str | None = None


class TrainParams(BaseModel):
    scan_id: uuid.UUID | None = None       # None → newest successful scan
    stop_atr: float = Field(default=1.5, ge=0.5, le=5.0)
    target_r: float = Field(default=2.0, ge=1.0, le=10.0)
    time_stop: int = Field(default=20, ge=5, le=60)
    trigger_window: int = Field(default=30, ge=5, le=120)
    label_kind: str = Field(default="auto", pattern="^(auto|target_vs_rest|r_ge_1)$")


class TrainStatusOut(BaseModel):
    running: bool
    stage: str
    error: str | None
    model_id: str | None
    ml_installed: bool


class ModelOut(BaseModel):
    id: str
    created_at: str
    status: str
    is_active: bool
    label_kind: str | None
    scan_id: str | None
    n_train: int
    n_valid: int
    train_end_date: str | None
    valid_start_date: str | None
    params: dict
    metrics: dict
    top_importances: dict


class BackfillStatusOut(BaseModel):
    running: bool
    done_symbols: int
    total_symbols: int
    error: str | None


@router.post("/train")
async def train(body: TrainParams, background_tasks: BackgroundTasks) -> dict:
    global _training, _train_error
    if not ml_available():
        raise HTTPException(
            status_code=400,
            detail='ML dependencies not installed — run: pip install -e ".[ml]" in backend/',
        )
    if _training:
        raise HTTPException(status_code=409, detail="Training already running.")
    _training = True
    _train_error = None
    _train_progress.stage = "starting"
    background_tasks.add_task(_train_bg, body)
    return {"status": "running"}


async def _train_bg(params: TrainParams) -> None:
    global _training, _train_error, _last_model_id
    from app.db.session import SessionLocal
    try:
        async with SessionLocal() as session:
            model_id = await train_ranker(
                session,
                scan_id=params.scan_id,
                stop_atr=params.stop_atr,
                target_r=params.target_r,
                time_stop=params.time_stop,
                trigger_window=params.trigger_window,
                label_kind=params.label_kind,
                progress=_train_progress,
            )
            _last_model_id = str(model_id)
    except Exception as exc:
        log.exception("ranker training failed")
        _train_error = str(exc)[:480]
    finally:
        _training = False


@router.get("/status")
async def train_status() -> TrainStatusOut:
    return TrainStatusOut(
        running=_training,
        stage=_train_progress.stage,
        error=_train_error,
        model_id=_last_model_id,
        ml_installed=ml_available(),
    )


@router.get("/model")
async def get_model(session: AsyncSession = Depends(get_session)) -> ModelOut:
    """The active model if one exists, else the most recent one."""
    q = await session.execute(
        select(MLModel)
        .order_by(MLModel.is_active.desc(), MLModel.created_at.desc())
        .limit(1)
    )
    row = q.scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No model trained yet.")
    importances = row.feature_importances or {}
    top = dict(sorted(importances.items(), key=lambda kv: -kv[1])[:30])
    return ModelOut(
        id=str(row.id),
        created_at=row.created_at.isoformat(),
        status=row.status,
        is_active=row.is_active,
        label_kind=row.label_kind,
        scan_id=str(row.scan_id) if row.scan_id else None,
        n_train=row.n_train,
        n_valid=row.n_valid,
        train_end_date=row.train_end_date.isoformat() if row.train_end_date else None,
        valid_start_date=row.valid_start_date.isoformat() if row.valid_start_date else None,
        params=row.params or {},
        metrics=row.metrics or {},
        top_importances=top,
    )


@router.post("/backfill-bars")
async def backfill_bars(background_tasks: BackgroundTasks) -> dict:
    """One-off full-history re-ingest (LOOKBACK_YEARS, currently 5y) for the
    whole active universe + benchmarks. Run once before the first 1260-day scan."""
    global _backfill_running, _backfill_error, _backfill_done_symbols
    if _backfill_running:
        raise HTTPException(status_code=409, detail="Backfill already running.")
    _backfill_running = True
    _backfill_error = None
    _backfill_done_symbols = 0
    background_tasks.add_task(_backfill_bg)
    return {"status": "running"}


async def _backfill_bg() -> None:
    global _backfill_running, _backfill_error
    global _backfill_done_symbols, _backfill_total_symbols
    from app.db.session import SessionLocal
    from app.services.eod_service import resync_full_history

    def on_chunk(n: int) -> None:
        global _backfill_done_symbols
        _backfill_done_symbols += n

    try:
        async with SessionLocal() as session:
            from app.db.models import ScreenerSymbol
            count_q = await session.execute(
                select(ScreenerSymbol.symbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
            )
            _backfill_total_symbols = len(count_q.all()) + 2  # + benchmarks
            counts = await resync_full_history(session, on_chunk=on_chunk)
            log.info(
                "5y backfill complete: %d symbols, %d with data",
                len(counts), sum(1 for v in counts.values() if v > 0),
            )
    except Exception as exc:
        log.exception("bars backfill failed")
        _backfill_error = str(exc)[:480]
    finally:
        _backfill_running = False


@router.get("/backfill-bars/status")
async def backfill_status() -> BackfillStatusOut:
    return BackfillStatusOut(
        running=_backfill_running,
        done_symbols=_backfill_done_symbols,
        total_symbols=_backfill_total_symbols,
        error=_backfill_error,
    )
