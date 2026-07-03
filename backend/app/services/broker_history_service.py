"""Broker history sync + round-trip trade reconstruction.

Three concerns, kept in one module because they are tightly coupled:

  1. `sync_executions_for_user`     — pull raw fills from the broker into
                                       `broker_executions` (idempotent).
  2. `rebuild_trades_for_user`      — FIFO-match buys/sells per (account,
                                       symbol) and persist round-trip
                                       `broker_trades` rows.
  3. `reconcile_with_tickets`        — attach broker_trades to existing
                                       Ticket rows where dates + symbol line up,
                                       so they don't double-count in the journal.

Why these are separate stages: re-deriving trades is cheap and lets us tweak
matching logic (FIFO vs LIFO, or a new commission split) without re-fetching
from the broker. Sync is the only stage that talks to the network.

Constraints to be aware of:
  • Questrade caps each activities request to a ~31-day window. We chunk in
    30-day buckets to stay safely under it.
  • We use /activities (not /executions) as the source — /executions has
    only ~30-day retention regardless of startTime, which silently truncates
    longer backfills. /activities goes back to account opening.
  • Sells that consume more shares than we've seen buys for are logged as
    "orphan close" and skipped (likely pre-window history we never fetched).
    User can extend the backfill window if they care.
  • Options executions use a different symbol format. Skipped for v1.
  • Cross-account trades are NOT matched (FIFO is per account+symbol). Buying
    NVDA in TFSA and selling in margin won't reconcile.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerExecution as BrokerExecutionDTO
from app.brokers.registry import get_broker
from app.db.models import (
    Account,
    BrokerExecution,
    BrokerSyncState,
    BrokerTrade,
    Ticket,
)

log = logging.getLogger(__name__)

CHUNK_DAYS = 30
DEFAULT_BACKFILL_YEARS = 2

# Heuristic: Questrade option symbols include a maturity + strike substring like
# "AAPL26Jun26C200.00". Stocks are short tickers. We skip anything that's
# clearly an option in v1.
def _looks_like_option(symbol: str) -> bool:
    if not symbol:
        return True
    if len(symbol) > 12 and any(c.isdigit() for c in symbol):
        # Heuristic: long symbol with embedded digits = likely option
        return True
    return False


# ─── Sync from broker ─────────────────────────────────────────────────────────

@dataclass
class SyncProgress:
    account_id: str
    fetched: int
    new_inserted: int
    skipped_options: int
    chunks_done: int
    chunks_total: int
    error: str | None = None


async def sync_executions_for_account(
    session: AsyncSession,
    *,
    user_id: str,
    account: Account,
    start: datetime,
    end: datetime,
    wipe_first: bool = False,
) -> SyncProgress:
    """Pull trade activities from the broker for one account, in 30-day chunks.

    Idempotent: re-running over the same window inserts no duplicates
    (uses ON CONFLICT DO NOTHING on (user_id, broker_execution_id)).

    `wipe_first=True` deletes all existing BrokerExecution rows for this
    account before fetching — use this to recover from a previous bad sync
    (e.g. when we used /executions instead of /activities and only got 30
    days). Costs nothing because activities are the source of truth.
    """
    broker = get_broker(user_id=user_id)

    if wipe_first:
        log.info("wipe_first=True for account=%s, deleting prior executions", account.id)
        await session.execute(
            delete(BrokerExecution).where(
                BrokerExecution.user_id == user_id,
                BrokerExecution.account_id == account.id,
            )
        )
        await session.commit()
    chunks: list[tuple[datetime, datetime]] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), end)
        chunks.append((cur, nxt))
        cur = nxt

    progress = SyncProgress(
        account_id=str(account.id),
        fetched=0,
        new_inserted=0,
        skipped_options=0,
        chunks_done=0,
        chunks_total=len(chunks),
    )

    qt_account_id = account.questrade_account_id

    # Mark running
    await _upsert_sync_state(session, user_id=user_id, account_id=account.id,
                              status="running", error=None)
    await session.commit()

    for chunk_start, chunk_end in chunks:
        try:
            executions = await broker.get_activities(qt_account_id, chunk_start, chunk_end)
        except Exception as exc:
            log.warning("Activities fetch failed for %s %s-%s: %s",
                        qt_account_id, chunk_start, chunk_end, exc)
            await _upsert_sync_state(
                session, user_id=user_id, account_id=account.id,
                status="failed", error=str(exc)[:480],
            )
            await session.commit()
            progress.error = str(exc)
            return progress

        progress.fetched += len(executions)

        rows_to_insert = []
        for ex in executions:
            if _looks_like_option(ex.symbol):
                progress.skipped_options += 1
                continue
            if not ex.broker_execution_id:
                continue
            if ex.side not in ("buy", "sell"):
                continue
            rows_to_insert.append({
                "user_id": user_id,
                "account_id": account.id,
                "broker_execution_id": ex.broker_execution_id,
                "broker_order_id": ex.broker_order_id,
                "symbol": ex.symbol,
                "currency": ex.currency,
                "side": ex.side,
                "quantity": ex.quantity,
                "price": ex.price,
                "commission": ex.commission,
                "executed_at": ex.executed_at,
                "venue": ex.venue,
                "raw": ex.raw,
            })

        if rows_to_insert:
            stmt = (
                pg_insert(BrokerExecution.__table__)
                .values(rows_to_insert)
                .on_conflict_do_nothing(constraint="uq_broker_exec_user_id")
            )
            result = await session.execute(stmt)
            progress.new_inserted += result.rowcount or 0
            await session.commit()

        progress.chunks_done += 1

    await _upsert_sync_state(
        session, user_id=user_id, account_id=account.id,
        status="success", error=None, last_synced_through=end,
    )
    await session.commit()
    return progress


async def sync_executions_for_user(
    session: AsyncSession,
    *,
    user_id: str,
    backfill_years: int = DEFAULT_BACKFILL_YEARS,
    full_resync: bool = False,
) -> list[SyncProgress]:
    """Pull trade activities for every active account belonging to a user.

    Per-account: starts from `last_synced_through` if known, else
    `backfill_years` back. End is now.

    `full_resync=True` ignores the high-water mark, wipes existing per-account
    rows, and refetches the entire `backfill_years` window. Use when:
      • Migrating from a broken sync (e.g. /executions → /activities)
      • Switching backfill_years to a larger value than before
      • Suspecting stale or corrupted data
    """
    accounts_q = await session.execute(
        select(Account).where(Account.user_id == user_id, Account.is_active == True)  # noqa: E712
    )
    accounts = accounts_q.scalars().all()
    if not accounts:
        return []

    results: list[SyncProgress] = []
    now = datetime.now(timezone.utc)
    for acct in accounts:
        sync_state_q = await session.execute(
            select(BrokerSyncState).where(
                BrokerSyncState.user_id == user_id,
                BrokerSyncState.account_id == acct.id,
            )
        )
        state = sync_state_q.scalar_one_or_none()
        if full_resync:
            start = now - timedelta(days=365 * backfill_years)
        elif state and state.last_synced_through:
            # Re-fetch last 24h on top of high-water — covers same-day late prints.
            start = state.last_synced_through - timedelta(days=1)
        else:
            start = now - timedelta(days=365 * backfill_years)

        progress = await sync_executions_for_account(
            session, user_id=user_id, account=acct, start=start, end=now,
            wipe_first=full_resync,
        )
        results.append(progress)

    return results


async def _upsert_sync_state(
    session: AsyncSession,
    *,
    user_id: str,
    account_id,
    status: str,
    error: str | None,
    last_synced_through: datetime | None = None,
) -> None:
    """Upsert the per-account sync state row."""
    now = datetime.now(timezone.utc)
    values = {
        "user_id": user_id,
        "account_id": account_id,
        "last_sync_status": status,
        "last_error": error,
        "last_synced_at": now,
    }
    if last_synced_through is not None:
        values["last_synced_through"] = last_synced_through

    update_cols = {
        "last_sync_status": status,
        "last_error": error,
        "last_synced_at": now,
    }
    if last_synced_through is not None:
        update_cols["last_synced_through"] = last_synced_through

    stmt = (
        pg_insert(BrokerSyncState.__table__)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_broker_sync_user_account",
            set_=update_cols,
        )
    )
    await session.execute(stmt)


# ─── FIFO matcher ─────────────────────────────────────────────────────────────

@dataclass
class _OpenLot:
    """One open buy lot. Gets consumed (potentially partially) by sells."""
    execution_id: str
    qty_remaining: Decimal
    qty_original: Decimal
    price: Decimal
    commission: Decimal           # commission on the full original buy
    executed_at: datetime


async def rebuild_trades_for_user(
    session: AsyncSession,
    *,
    user_id: str,
) -> int:
    """Wipe and rebuild all `broker_trades` rows for a user from their
    `broker_executions`. Returns count of trades produced.

    Per (account, symbol, currency) we walk fills chronologically, push buy
    lots onto a FIFO queue, and on each sell consume from the oldest lots.
    Each sell can produce 1 or more BrokerTrade rows (one per source lot).
    """
    # Wipe existing trades for this user
    await session.execute(delete(BrokerTrade).where(BrokerTrade.user_id == user_id))
    await session.commit()

    # Pull all executions
    exec_q = await session.execute(
        select(BrokerExecution)
        .where(BrokerExecution.user_id == user_id)
        .order_by(BrokerExecution.executed_at.asc(), BrokerExecution.id.asc())
    )
    executions = exec_q.scalars().all()
    if not executions:
        return 0

    # Group by (account_id, symbol, currency)
    grouped: dict[tuple, list[BrokerExecution]] = defaultdict(list)
    for ex in executions:
        grouped[(ex.account_id, ex.symbol, ex.currency)].append(ex)

    rows_out: list[dict] = []
    orphan_closes = 0

    for (account_id, symbol, currency), fills in grouped.items():
        open_lots: deque[_OpenLot] = deque()

        for ex in fills:
            qty = Decimal(ex.quantity)
            price = Decimal(ex.price)
            commission = Decimal(ex.commission)

            if ex.side == "buy":
                open_lots.append(_OpenLot(
                    execution_id=ex.broker_execution_id,
                    qty_remaining=qty,
                    qty_original=qty,
                    price=price,
                    commission=commission,
                    executed_at=ex.executed_at,
                ))
                continue

            # SELL — consume from oldest lots
            sell_remaining = qty
            sell_commission = commission

            # Collect per-lot consumption so we can build one BrokerTrade per
            # source-lot. We DON'T merge across lots because the entry prices
            # are different — Tradervue-style "one trade per close × source lot".
            while sell_remaining > 0 and open_lots:
                lot = open_lots[0]
                take = min(lot.qty_remaining, sell_remaining)

                # Allocate commissions proportionally
                entry_comm_alloc = (
                    lot.commission * (take / lot.qty_original)
                    if lot.qty_original > 0 else Decimal(0)
                )
                exit_comm_alloc = (
                    sell_commission * (take / qty) if qty > 0 else Decimal(0)
                )

                gross_pnl = (price - lot.price) * take
                net_pnl = gross_pnl - entry_comm_alloc - exit_comm_alloc

                cost_basis = lot.price * take + entry_comm_alloc
                pnl_pct = (net_pnl / cost_basis) if cost_basis > 0 else None

                hold_days = max(0, (ex.executed_at - lot.executed_at).days)

                rows_out.append({
                    "user_id": user_id,
                    "account_id": account_id,
                    "symbol": symbol,
                    "currency": currency,
                    "shares": take,
                    "avg_entry_price": lot.price,
                    "avg_exit_price": price,
                    "entry_commission": entry_comm_alloc,
                    "exit_commission": exit_comm_alloc,
                    "entry_date": lot.executed_at,
                    "exit_date": ex.executed_at,
                    "hold_days": hold_days,
                    "realized_pnl": net_pnl,
                    "realized_pnl_pct": pnl_pct,
                    "r_multiple": None,    # filled later if we attach a ticket with a stop
                    "ticket_id": None,
                    "setup_type": "manual",
                    "close_reason_tag": None,
                    "notes": None,
                    "entry_execution_ids": [lot.execution_id],
                    "exit_execution_ids": [ex.broker_execution_id],
                })

                lot.qty_remaining -= take
                sell_remaining -= take
                if lot.qty_remaining <= 0:
                    open_lots.popleft()

            if sell_remaining > 0:
                # No matching buys — short sale or pre-window history.
                orphan_closes += 1
                log.info(
                    "Orphan close: user=%s account=%s symbol=%s sold %s shares with no matching buys",
                    user_id, account_id, symbol, sell_remaining,
                )

    if rows_out:
        # Bulk insert
        await session.execute(pg_insert(BrokerTrade.__table__), rows_out)
        await session.commit()

    if orphan_closes:
        log.warning("rebuild_trades: %d orphan closes for user=%s", orphan_closes, user_id)

    return len(rows_out)


# ─── Reconciliation with existing tickets ─────────────────────────────────────

async def reconcile_with_tickets(
    session: AsyncSession,
    *,
    user_id: str,
    entry_tolerance_days: int = 3,
) -> int:
    """Attach BrokerTrade rows to existing Ticket rows where the symbol matches
    and the broker entry_date is within `entry_tolerance_days` of the ticket's
    filled_at (or armed_at if no fill timestamp).

    If a ticket has a stop_price, also computes r_multiple = (exit - entry) /
    (entry - stop) and copies setup_type + close_reason_tag onto the trade.

    Returns count of reconciliations made.
    """
    # Load all (real, not paper) tickets for the user
    ticket_q = await session.execute(
        select(Ticket).where(
            Ticket.user_id == user_id,
            Ticket.is_paper == False,  # noqa: E712
        )
    )
    tickets = ticket_q.scalars().all()
    if not tickets:
        return 0

    # Index by (symbol, currency)
    by_symbol: dict[tuple[str, str], list[Ticket]] = defaultdict(list)
    for t in tickets:
        by_symbol[(t.symbol, t.currency)].append(t)

    trade_q = await session.execute(
        select(BrokerTrade).where(
            BrokerTrade.user_id == user_id,
            BrokerTrade.ticket_id.is_(None),
        )
    )
    trades = trade_q.scalars().all()

    reconciled = 0
    tolerance = timedelta(days=entry_tolerance_days)

    for trade in trades:
        candidates = by_symbol.get((trade.symbol, trade.currency), [])
        best: Ticket | None = None
        best_delta: timedelta | None = None
        for tk in candidates:
            anchor = tk.filled_at or tk.armed_at or tk.created_at
            if anchor is None:
                continue
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            delta = abs(trade.entry_date - anchor)
            if delta > tolerance:
                continue
            if best is None or (best_delta is not None and delta < best_delta):
                best = tk
                best_delta = delta

        if best is None:
            continue

        trade.ticket_id = best.id
        trade.setup_type = best.setup_type or "manual"
        trade.close_reason_tag = best.close_reason_tag

        # If the ticket has a stop, compute r_multiple
        if best.stop_price and trade.avg_entry_price > best.stop_price:
            risk_per_share = Decimal(trade.avg_entry_price) - Decimal(best.stop_price)
            if risk_per_share > 0:
                trade.r_multiple = (Decimal(trade.avg_exit_price) - Decimal(trade.avg_entry_price)) / risk_per_share
        reconciled += 1

    if reconciled:
        await session.commit()
    return reconciled


# ─── One-call orchestrator for the API layer ──────────────────────────────────

@dataclass
class FullSyncResult:
    accounts_synced: int
    executions_fetched: int
    executions_inserted: int
    trades_built: int
    trades_reconciled: int
    errors: list[str]
    cash_flows_inserted: int = 0


async def full_sync_for_user(
    session: AsyncSession,
    *,
    user_id: str,
    backfill_years: int = DEFAULT_BACKFILL_YEARS,
    full_resync: bool = False,
) -> FullSyncResult:
    """End-to-end: pull trade activities → rebuild trades → reconcile with tickets.

    `full_resync=True` wipes existing executions and refetches everything.
    Pass when the user explicitly requests a clean rebuild from the UI.
    """
    progresses = await sync_executions_for_user(
        session, user_id=user_id, backfill_years=backfill_years,
        full_resync=full_resync,
    )
    errors = [p.error for p in progresses if p.error]

    trades_built = await rebuild_trades_for_user(session, user_id=user_id)
    reconciled = await reconcile_with_tickets(session, user_id=user_id)

    # Cash flows (deposits/withdrawals/transfers) ride along with the same
    # sync button — they power the charter honesty page's counterfactual.
    from app.services.charter_service import sync_cash_flows_for_user
    try:
        cash_flows = await sync_cash_flows_for_user(
            session, user_id=user_id, backfill_years=backfill_years,
        )
    except Exception as exc:
        log.exception("Cash-flow sync failed for user=%s", user_id)
        errors.append(f"cash flows: {exc}")
        cash_flows = 0

    return FullSyncResult(
        accounts_synced=len(progresses),
        executions_fetched=sum(p.fetched for p in progresses),
        executions_inserted=sum(p.new_inserted for p in progresses),
        trades_built=trades_built,
        trades_reconciled=reconciled,
        errors=errors,
        cash_flows_inserted=cash_flows,
    )
