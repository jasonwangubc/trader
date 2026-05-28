"""Broker history API — manual trade ingestion via Questrade executions.

POST /api/broker-history/sync       Trigger a backfill (background task).
GET  /api/broker-history/status     Per-account sync state.
GET  /api/broker-history/trades     List reconstructed round-trip trades.
POST /api/broker-history/trades/{id}/tag   Tag setup_type / notes for a trade.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_id
from app.db.models import Account, BrokerSyncState, BrokerTrade
from app.db.session import get_session, SessionLocal
from app.services.broker_history_service import (
    DEFAULT_BACKFILL_YEARS,
    FullSyncResult,
    full_sync_for_user,
)

router = APIRouter(prefix="/api/broker-history", tags=["broker-history"])
log = logging.getLogger(__name__)


# Single-user lock — only one sync at a time per user
_user_syncs_running: set[str] = set()


class SyncRequest(BaseModel):
    backfill_years: int = Field(default=DEFAULT_BACKFILL_YEARS, ge=1, le=7)
    full_resync: bool = Field(default=False, description="Wipe and refetch everything (use after a bad sync or to widen the window)")


class SyncStartAck(BaseModel):
    status: str   # "started" | "already_running"
    user_id: str
    backfill_years: int
    full_resync: bool


@router.post("/sync", response_model=SyncStartAck)
async def start_sync(
    body: SyncRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
) -> SyncStartAck:
    if user_id in _user_syncs_running:
        return SyncStartAck(status="already_running", user_id=user_id, backfill_years=body.backfill_years, full_resync=body.full_resync)
    _user_syncs_running.add(user_id)
    background_tasks.add_task(_run_sync, user_id, body.backfill_years, body.full_resync)
    return SyncStartAck(status="started", user_id=user_id, backfill_years=body.backfill_years, full_resync=body.full_resync)


async def _run_sync(user_id: str, backfill_years: int, full_resync: bool) -> None:
    try:
        async with SessionLocal() as session:
            result = await full_sync_for_user(
                session, user_id=user_id, backfill_years=backfill_years,
                full_resync=full_resync,
            )
            log.info(
                "broker-history sync done for %s: accts=%d fetched=%d new=%d trades=%d reconciled=%d errors=%s",
                user_id, result.accounts_synced, result.executions_fetched,
                result.executions_inserted, result.trades_built,
                result.trades_reconciled, result.errors,
            )
    except Exception:
        log.exception("broker-history sync failed for user=%s", user_id)
    finally:
        _user_syncs_running.discard(user_id)


class AccountSyncStatus(BaseModel):
    account_id: str
    questrade_account_id: str
    account_type: str
    last_synced_through: datetime | None
    last_sync_status: str
    last_synced_at: datetime | None
    last_error: str | None
    executions_count: int
    trades_count: int


class SyncStatusOut(BaseModel):
    running: bool
    user_id: str
    accounts: list[AccountSyncStatus]
    total_executions: int
    total_trades: int
    reconciled_trades: int


@router.get("/status", response_model=SyncStatusOut)
async def status(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> SyncStatusOut:
    accounts_q = await session.execute(
        select(Account).where(Account.user_id == user_id, Account.is_active == True)  # noqa: E712
    )
    accounts = accounts_q.scalars().all()

    sync_q = await session.execute(
        select(BrokerSyncState).where(BrokerSyncState.user_id == user_id)
    )
    sync_states = {s.account_id: s for s in sync_q.scalars().all()}

    out_accounts: list[AccountSyncStatus] = []
    for acct in accounts:
        st = sync_states.get(acct.id)
        exec_count_q = await session.execute(
            select(func.count()).select_from(
                __import__("app.db.models", fromlist=["BrokerExecution"]).BrokerExecution
            ).where(
                __import__("app.db.models", fromlist=["BrokerExecution"]).BrokerExecution.account_id == acct.id
            )
        )
        exec_count = exec_count_q.scalar() or 0
        trade_count_q = await session.execute(
            select(func.count()).select_from(BrokerTrade).where(BrokerTrade.account_id == acct.id)
        )
        trade_count = trade_count_q.scalar() or 0

        out_accounts.append(AccountSyncStatus(
            account_id=str(acct.id),
            questrade_account_id=acct.questrade_account_id,
            account_type=acct.type,
            last_synced_through=st.last_synced_through if st else None,
            last_sync_status=st.last_sync_status if st else "never",
            last_synced_at=st.last_synced_at if st else None,
            last_error=st.last_error if st else None,
            executions_count=exec_count,
            trades_count=trade_count,
        ))

    total_exec_q = await session.execute(
        select(func.count()).select_from(
            __import__("app.db.models", fromlist=["BrokerExecution"]).BrokerExecution
        ).where(
            __import__("app.db.models", fromlist=["BrokerExecution"]).BrokerExecution.user_id == user_id
        )
    )
    total_trade_q = await session.execute(
        select(func.count()).select_from(BrokerTrade).where(BrokerTrade.user_id == user_id)
    )
    reconciled_q = await session.execute(
        select(func.count()).select_from(BrokerTrade).where(
            BrokerTrade.user_id == user_id,
            BrokerTrade.ticket_id.isnot(None),
        )
    )

    return SyncStatusOut(
        running=user_id in _user_syncs_running,
        user_id=user_id,
        accounts=out_accounts,
        total_executions=total_exec_q.scalar() or 0,
        total_trades=total_trade_q.scalar() or 0,
        reconciled_trades=reconciled_q.scalar() or 0,
    )


class BrokerTradeOut(BaseModel):
    id: str
    account_id: str
    account_type: str | None = None
    symbol: str
    currency: str
    shares: float
    avg_entry_price: float
    avg_exit_price: float
    entry_date: datetime
    exit_date: datetime
    hold_days: int
    realized_pnl: float
    realized_pnl_pct: float | None = None
    r_multiple: float | None = None
    setup_type: str
    close_reason_tag: str | None = None
    notes: str | None = None
    ticket_id: str | None = None
    is_managed: bool      # = has ticket attached


class TradesListOut(BaseModel):
    trades: list[BrokerTradeOut]
    total: int
    realized_pnl_total: float
    realized_pnl_by_currency: dict[str, float]
    wins: int
    losses: int
    scratches: int
    managed_count: int    # = trades linked to tickets
    manual_count: int


@router.get("/trades", response_model=TradesListOut)
async def list_trades(
    limit: int = Query(default=200, ge=1, le=2000),
    symbol: str | None = Query(default=None),
    setup_type: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> TradesListOut:
    q = select(BrokerTrade).where(BrokerTrade.user_id == user_id)
    if symbol:
        q = q.where(BrokerTrade.symbol == symbol.upper())
    if setup_type:
        q = q.where(BrokerTrade.setup_type == setup_type)
    q = q.order_by(desc(BrokerTrade.exit_date)).limit(limit)
    result = await session.execute(q)
    trades = result.scalars().all()

    # Account type lookup
    accounts_q = await session.execute(
        select(Account).where(Account.user_id == user_id)
    )
    acct_type_by_id = {a.id: a.type for a in accounts_q.scalars().all()}

    out: list[BrokerTradeOut] = []
    pnl_by_ccy: dict[str, float] = {}
    wins = losses = scratches = managed = manual = 0
    for t in trades:
        pnl = float(t.realized_pnl)
        pnl_by_ccy[t.currency] = pnl_by_ccy.get(t.currency, 0.0) + pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        else:
            scratches += 1
        if t.ticket_id is not None:
            managed += 1
        else:
            manual += 1
        out.append(BrokerTradeOut(
            id=str(t.id),
            account_id=str(t.account_id),
            account_type=acct_type_by_id.get(t.account_id),
            symbol=t.symbol,
            currency=t.currency,
            shares=float(t.shares),
            avg_entry_price=float(t.avg_entry_price),
            avg_exit_price=float(t.avg_exit_price),
            entry_date=t.entry_date,
            exit_date=t.exit_date,
            hold_days=t.hold_days,
            realized_pnl=pnl,
            realized_pnl_pct=float(t.realized_pnl_pct) if t.realized_pnl_pct is not None else None,
            r_multiple=float(t.r_multiple) if t.r_multiple is not None else None,
            setup_type=t.setup_type,
            close_reason_tag=t.close_reason_tag,
            notes=t.notes,
            ticket_id=str(t.ticket_id) if t.ticket_id else None,
            is_managed=t.ticket_id is not None,
        ))

    # totals from a separate query so they cover the full filtered set, not just the limit
    sum_q = select(func.sum(BrokerTrade.realized_pnl)).where(BrokerTrade.user_id == user_id)
    if symbol:
        sum_q = sum_q.where(BrokerTrade.symbol == symbol.upper())
    if setup_type:
        sum_q = sum_q.where(BrokerTrade.setup_type == setup_type)
    total_pnl = (await session.execute(sum_q)).scalar() or 0

    count_q = select(func.count()).select_from(BrokerTrade).where(BrokerTrade.user_id == user_id)
    if symbol:
        count_q = count_q.where(BrokerTrade.symbol == symbol.upper())
    if setup_type:
        count_q = count_q.where(BrokerTrade.setup_type == setup_type)
    total_count = (await session.execute(count_q)).scalar() or 0

    return TradesListOut(
        trades=out,
        total=total_count,
        realized_pnl_total=float(total_pnl),
        realized_pnl_by_currency=pnl_by_ccy,
        wins=wins,
        losses=losses,
        scratches=scratches,
        managed_count=managed,
        manual_count=manual,
    )


class TagRequest(BaseModel):
    setup_type: str | None = None
    notes: str | None = None
    close_reason_tag: str | None = None


@router.post("/trades/{trade_id}/tag", response_model=BrokerTradeOut)
async def tag_trade(
    trade_id: str,
    body: TagRequest,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> BrokerTradeOut:
    """Update setup_type / notes / close_reason_tag on a broker trade.

    For manual trades where the screener didn't fire a ticket, this is how the
    user retroactively classifies what kind of setup they took (VCP, manual,
    swing, etc.) — without which behavioral stats are noise.
    """
    trade = await session.get(BrokerTrade, trade_id)
    if trade is None or trade.user_id != user_id:
        raise HTTPException(status_code=404, detail="Trade not found")
    if body.setup_type is not None:
        trade.setup_type = body.setup_type
    if body.notes is not None:
        trade.notes = body.notes
    if body.close_reason_tag is not None:
        trade.close_reason_tag = body.close_reason_tag
    await session.commit()

    return BrokerTradeOut(
        id=str(trade.id),
        account_id=str(trade.account_id),
        symbol=trade.symbol,
        currency=trade.currency,
        shares=float(trade.shares),
        avg_entry_price=float(trade.avg_entry_price),
        avg_exit_price=float(trade.avg_exit_price),
        entry_date=trade.entry_date,
        exit_date=trade.exit_date,
        hold_days=trade.hold_days,
        realized_pnl=float(trade.realized_pnl),
        realized_pnl_pct=float(trade.realized_pnl_pct) if trade.realized_pnl_pct is not None else None,
        r_multiple=float(trade.r_multiple) if trade.r_multiple is not None else None,
        setup_type=trade.setup_type,
        close_reason_tag=trade.close_reason_tag,
        notes=trade.notes,
        ticket_id=str(trade.ticket_id) if trade.ticket_id else None,
        is_managed=trade.ticket_id is not None,
    )
