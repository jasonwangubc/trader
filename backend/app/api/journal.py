"""Trade journal summary — aggregates closed ticket outcomes into performance metrics."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import csv
import io

from fastapi.responses import StreamingResponse

from app.brokers.registry import get_broker
from app.db.models import Account, BrokerTrade, Fill, Order, OrderIntent, Position, Ticket, TicketStatus
from app.db.session import get_session
from app.api.auth import get_user_id
from app.services.accounts_service import get_household_equity
from app.services.coach_service import Insight, compute_insights
from app.services.positions_service import fetch_broker_stop_targets, is_cash_equivalent

router = APIRouter(prefix="/api/journal", tags=["journal"])


# ── Open risk dashboard ──────────────────────────────────────────────────────

class OpenPosition(BaseModel):
    """A real or paper ticket-managed position with a defined stop."""
    symbol: str
    currency: str
    shares: int
    entry_price: Decimal | None
    stop_price: Decimal
    open_risk_dollars: Decimal       # (entry - stop) × shares
    open_r_multiple: Decimal | None  # reserved (needs live quote — not yet computed)
    sector: str | None
    account_type: str
    is_paper: bool


class UnmanagedPosition(BaseModel):
    """A real position held at the broker with no ticket attached.

    Without a ticket we don't track its risk in the journal, but the position
    might still have a sell-stop placed at the broker — in which case the UI
    shows a milder warning ('unmanaged but stopped') rather than red alarm.
    """
    symbol: str
    currency: str
    shares: int
    market_value: Decimal
    broker_stop_price: Decimal | None = None
    broker_target_price: Decimal | None = None
    position_id: str | None = None


class PendingOrder(BaseModel):
    """A pending entry order at the broker (limit-buy or stop-buy not yet filled).

    Risk is bounded — best estimate uses an attached ticket's stop if we can
    match the symbol, otherwise we fall back to the order's own stop_price
    (for stop-limit), or just expose the notional commitment.
    """
    symbol: str
    currency: str
    side: str           # "buy" / "sell"
    order_type: str     # "limit" / "stop_market" / "stop_limit" / "market"
    quantity: int
    limit_price: Decimal | None
    stop_price: Decimal | None
    notional: Decimal               # quantity × (limit or stop price)
    est_risk_dollars: Decimal | None  # if we can attach a ticket stop
    matched_ticket_id: str | None
    is_paper: bool


class OpenRiskSummary(BaseModel):
    # Real ticket-managed positions
    positions: list[OpenPosition]
    total_risk_usd: Decimal
    total_risk_cad: Decimal

    # Paper sandbox (separate so it doesn't pollute real %)
    paper_positions: list[OpenPosition]
    paper_risk_usd: Decimal
    paper_risk_cad: Decimal

    # Pending entry orders (not yet filled)
    pending_orders: list[PendingOrder]
    pending_notional_usd: Decimal
    pending_notional_cad: Decimal
    pending_risk_usd: Decimal
    pending_risk_cad: Decimal

    # Real broker positions with no ticket / no stop attached (count + market value)
    unmanaged_positions: list[UnmanagedPosition]
    unmanaged_value_usd: Decimal
    unmanaged_value_cad: Decimal

    # Equity + percentages
    total_equity_usd: Decimal
    total_equity_cad: Decimal
    risk_pct_usd: Decimal           # real risk only / equity
    risk_pct_cad: Decimal
    pending_pct_usd: Decimal        # if-all-fill risk / equity
    pending_pct_cad: Decimal

    max_risk_pct: Decimal           # 8% cap
    warning: str | None
    pending_orders_supported: bool  # false if broker can't list open orders


@router.get("/risk", response_model=OpenRiskSummary)
async def open_risk(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> OpenRiskSummary:
    """Aggregate open risk across real ticketed positions, paper trades,
    pending broker orders, and unmanaged broker holdings.

    - Cash equivalents (TBIL / SGOV / CASH.TO etc.) are excluded everywhere.
    - Paper trades are kept separate so they don't pollute the real-account %.
    - Manual/unmanaged real positions show a count + warning, no $ risk
      (no stop = no defined risk).
    - Pending entry orders are bucketed separately so the user can see
      "if all my pending orders fill, my risk would become X%".
    """
    MAX_RISK_PCT = Decimal("0.08")

    # ── Filled tickets — split paper vs real, exclude cash equivalents ──────
    filled_result = await session.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.FILLED.value,
            Ticket.user_id == user_id,
        )
    )
    filled_tickets = [t for t in filled_result.scalars().all() if not is_cash_equivalent(t.symbol)]

    equity = await get_household_equity(session, user_id=user_id)
    total_usd = equity.get("USD", Decimal(0))
    total_cad = equity.get("CAD", Decimal(0))

    real_positions: list[OpenPosition] = []
    paper_positions: list[OpenPosition] = []
    real_usd = Decimal(0)
    real_cad = Decimal(0)
    paper_usd = Decimal(0)
    paper_cad = Decimal(0)

    # Index of (symbol, currency) → real ticket stop, for matching pending orders
    real_ticket_stops: dict[tuple[str, str], Decimal] = {}

    for t in filled_tickets:
        fill_result = await session.execute(
            select(Fill).join(Order).where(
                Order.ticket_id == t.id,
                Order.intent == OrderIntent.ENTRY.value,
            ).order_by(Fill.occurred_at).limit(1)
        )
        fill = fill_result.scalar_one_or_none()
        entry_price = fill.price if fill else None

        per_share_risk = (entry_price - t.stop_price) if entry_price else (t.trigger_price - t.stop_price)
        open_risk_dollars = (per_share_risk * t.position_size_shares).quantize(Decimal("0.01"))

        account = await session.get(Account, t.account_id)
        account_type = account.type if account else "Unknown"

        pos = OpenPosition(
            symbol=t.symbol,
            currency=t.currency,
            shares=t.position_size_shares,
            entry_price=entry_price,
            stop_price=t.stop_price,
            open_risk_dollars=open_risk_dollars,
            open_r_multiple=None,
            sector=None,
            account_type=account_type,
            is_paper=t.is_paper,
        )
        if t.is_paper:
            paper_positions.append(pos)
            if t.currency == "USD":
                paper_usd += open_risk_dollars
            else:
                paper_cad += open_risk_dollars
        else:
            real_positions.append(pos)
            real_ticket_stops[(t.symbol, t.currency)] = t.stop_price
            if t.currency == "USD":
                real_usd += open_risk_dollars
            else:
                real_cad += open_risk_dollars

    # ── Unmanaged broker positions (real, no ticket linked) ─────────────────
    unmanaged_q = await session.execute(
        select(Position).join(Account).where(
            Account.user_id == user_id,
            Position.ticket_id.is_(None),
            Position.is_managed == False,             # noqa: E712
            Position.is_buy_and_hold == False,        # noqa: E712
            Position.quantity > 0,
        )
    )
    unmanaged_rows = [p for p in unmanaged_q.scalars().all() if not is_cash_equivalent(p.symbol)]

    # Best-effort lookup of broker-side stops/targets to distinguish
    # "unmanaged but stopped" from "unmanaged, exposed".
    broker_stop_targets: dict[tuple, object] = {}
    if unmanaged_rows:
        try:
            broker = get_broker(user_id=user_id)
            order_broker = getattr(broker, "_quote_source", broker)
            broker_stop_targets = await fetch_broker_stop_targets(
                user_id=user_id, session=session, broker=order_broker,
            )
        except Exception:
            broker_stop_targets = {}

    unmanaged: list[UnmanagedPosition] = []
    unmanaged_usd = Decimal(0)
    unmanaged_cad = Decimal(0)
    for p in unmanaged_rows:
        mv = p.market_value or Decimal(0)
        st = broker_stop_targets.get((p.account_id, p.symbol))
        unmanaged.append(UnmanagedPosition(
            symbol=p.symbol,
            currency=p.currency,
            shares=int(p.quantity),
            market_value=mv,
            broker_stop_price=getattr(st, "stop_price", None) if st else None,
            broker_target_price=getattr(st, "target_price", None) if st else None,
            position_id=str(p.id),
        ))
        if p.currency == "USD":
            unmanaged_usd += mv
        else:
            unmanaged_cad += mv

    # ── Pending entry orders from the broker ────────────────────────────────
    pending: list[PendingOrder] = []
    pending_notional_usd = Decimal(0)
    pending_notional_cad = Decimal(0)
    pending_risk_usd = Decimal(0)
    pending_risk_cad = Decimal(0)
    pending_supported = True

    try:
        broker = get_broker(user_id=user_id)
        accounts_q = await session.execute(
            select(Account).where(Account.user_id == user_id, Account.is_active == True)  # noqa: E712
        )
        accounts = accounts_q.scalars().all()

        # Map our DB ticket id ↔ broker_order_id, so a broker order placed by
        # our system can be tagged to its ticket (paper or real).
        order_q = await session.execute(
            select(Order).join(Ticket).where(
                Ticket.user_id == user_id,
                Order.broker_order_id.isnot(None),
                Order.intent == OrderIntent.ENTRY.value,
            )
        )
        broker_id_to_ticket: dict[str, Ticket] = {}
        for o in order_q.scalars().all():
            t = await session.get(Ticket, o.ticket_id)
            if t is not None:
                broker_id_to_ticket[o.broker_order_id] = t

        for acct in accounts:
            try:
                broker_orders = await broker.get_open_orders(acct.questrade_account_id)
            except Exception:
                continue
            for bo in broker_orders:
                if bo.side.lower() != "buy":
                    continue  # entry orders only
                if is_cash_equivalent(bo.symbol):
                    continue

                ref_price = bo.limit_price or bo.stop_price
                if ref_price is None:
                    continue
                notional = (Decimal(bo.quantity) * ref_price).quantize(Decimal("0.01"))

                # Estimate risk if we can attach a stop (own ticket or order's own stop)
                est_risk: Decimal | None = None
                matched_ticket = broker_id_to_ticket.get(bo.broker_order_id)
                is_paper_order = bool(matched_ticket and matched_ticket.is_paper)
                if matched_ticket is not None and matched_ticket.stop_price:
                    est_risk = ((ref_price - matched_ticket.stop_price) * bo.quantity).quantize(Decimal("0.01"))
                elif (bo.symbol, bo.currency) in real_ticket_stops:
                    stop = real_ticket_stops[(bo.symbol, bo.currency)]
                    est_risk = ((ref_price - stop) * bo.quantity).quantize(Decimal("0.01"))
                elif bo.order_type == "stop_limit" and bo.stop_price and bo.limit_price:
                    # rare on entry, but cap at limit-stop distance
                    est_risk = ((bo.limit_price - bo.stop_price).copy_abs() * bo.quantity).quantize(Decimal("0.01"))

                pending.append(PendingOrder(
                    symbol=bo.symbol,
                    currency=bo.currency,
                    side=bo.side,
                    order_type=bo.order_type,
                    quantity=bo.quantity,
                    limit_price=bo.limit_price,
                    stop_price=bo.stop_price,
                    notional=notional,
                    est_risk_dollars=est_risk,
                    matched_ticket_id=str(matched_ticket.id) if matched_ticket else None,
                    is_paper=is_paper_order,
                ))

                if is_paper_order:
                    continue  # paper pending orders don't add to real pending bucket

                if bo.currency == "USD":
                    pending_notional_usd += notional
                    if est_risk is not None and est_risk > 0:
                        pending_risk_usd += est_risk
                else:
                    pending_notional_cad += notional
                    if est_risk is not None and est_risk > 0:
                        pending_risk_cad += est_risk
    except Exception:
        # Broker not configured or unreachable — fall through with empty pending
        pending_supported = False

    # ── Percentages + warning ───────────────────────────────────────────────
    def _pct(num: Decimal, denom: Decimal) -> Decimal:
        return (num / denom).quantize(Decimal("0.0001")) if denom > 0 else Decimal(0)

    risk_pct_usd    = _pct(real_usd, total_usd)
    risk_pct_cad    = _pct(real_cad, total_cad)
    pending_pct_usd = _pct(real_usd + pending_risk_usd, total_usd)
    pending_pct_cad = _pct(real_cad + pending_risk_cad, total_cad)

    warning = None
    max_pct = max(risk_pct_usd, risk_pct_cad)
    if max_pct >= MAX_RISK_PCT:
        warning = f"Real open risk ({float(max_pct)*100:.1f}%) is at or above the 8% cap. Do not add new positions."
    elif max_pct >= MAX_RISK_PCT * Decimal("0.75"):
        warning = f"Real open risk ({float(max_pct)*100:.1f}%) approaching 8% cap. Be selective with new entries."

    return OpenRiskSummary(
        positions=real_positions,
        total_risk_usd=real_usd.quantize(Decimal("0.01")),
        total_risk_cad=real_cad.quantize(Decimal("0.01")),

        paper_positions=paper_positions,
        paper_risk_usd=paper_usd.quantize(Decimal("0.01")),
        paper_risk_cad=paper_cad.quantize(Decimal("0.01")),

        pending_orders=pending,
        pending_notional_usd=pending_notional_usd.quantize(Decimal("0.01")),
        pending_notional_cad=pending_notional_cad.quantize(Decimal("0.01")),
        pending_risk_usd=pending_risk_usd.quantize(Decimal("0.01")),
        pending_risk_cad=pending_risk_cad.quantize(Decimal("0.01")),

        unmanaged_positions=unmanaged,
        unmanaged_value_usd=unmanaged_usd.quantize(Decimal("0.01")),
        unmanaged_value_cad=unmanaged_cad.quantize(Decimal("0.01")),

        total_equity_usd=total_usd,
        total_equity_cad=total_cad,
        risk_pct_usd=risk_pct_usd,
        risk_pct_cad=risk_pct_cad,
        pending_pct_usd=pending_pct_usd,
        pending_pct_cad=pending_pct_cad,
        max_risk_pct=MAX_RISK_PCT,
        warning=warning,
        pending_orders_supported=pending_supported,
    )

CLOSED_STATUSES = {
    TicketStatus.STOPPED_OUT.value,
    TicketStatus.TARGET_HIT.value,
    TicketStatus.FILLED.value,   # filled + outcome set = closed manually
}


class SetupBreakdown(BaseModel):
    setup_type: str
    trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: float
    avg_r: float
    total_r: float


class MonthBreakdown(BaseModel):
    month: str          # "YYYY-MM"
    trades: int
    win_rate: float
    avg_r: float
    total_r: float


class JournalSummary(BaseModel):
    total_trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: float
    avg_r_winner: float
    avg_r_loser: float
    expectancy: float           # avg R per trade = (win_rate * avg_winner) + ((1-win_rate) * avg_loser)
    profit_factor: float        # gross_wins / abs(gross_losses)
    total_r: float
    total_realized_pnl: float
    by_setup: list[SetupBreakdown]
    by_month: list[MonthBreakdown]
    equity_curve: list[dict]    # [{date, cumulative_r}] sorted ascending


@router.get("/summary", response_model=JournalSummary)
async def summary(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> JournalSummary:
    result = await session.execute(
        select(Ticket).where(
            Ticket.outcome.isnot(None),
            Ticket.r_multiple.isnot(None),
            Ticket.user_id == user_id,
            Ticket.is_paper == False,  # noqa: E712
        ).order_by(Ticket.closed_at.asc().nullslast())
    )
    tickets = result.scalars().all()

    if not tickets:
        return JournalSummary(
            total_trades=0, wins=0, losses=0, scratches=0,
            win_rate=0.0, avg_r_winner=0.0, avg_r_loser=0.0,
            expectancy=0.0, profit_factor=0.0, total_r=0.0,
            total_realized_pnl=0.0, by_setup=[], by_month=[],
            equity_curve=[],
        )

    wins = [t for t in tickets if t.outcome == "win"]
    losses = [t for t in tickets if t.outcome == "loss"]
    scratches = [t for t in tickets if t.outcome == "scratch"]

    def avg_r(ts):
        rs = [float(t.r_multiple) for t in ts if t.r_multiple]
        return sum(rs) / len(rs) if rs else 0.0

    n = len(tickets)
    win_rate = len(wins) / n if n else 0.0
    avg_winner = avg_r(wins)
    avg_loser = avg_r(losses)
    expectancy = win_rate * avg_winner + (1 - win_rate) * avg_loser

    gross_wins = sum(float(t.r_multiple) for t in wins if t.r_multiple and float(t.r_multiple) > 0)
    gross_loss = abs(sum(float(t.r_multiple) for t in losses if t.r_multiple and float(t.r_multiple) < 0))
    profit_factor = gross_wins / gross_loss if gross_loss > 0 else float("inf") if gross_wins > 0 else 0.0

    total_r = sum(float(t.r_multiple or 0) for t in tickets)
    total_pnl = sum(float(t.realized_pnl or 0) for t in tickets)

    # By-setup breakdown
    from collections import defaultdict
    by_setup_map: dict[str, list] = defaultdict(list)
    for t in tickets:
        by_setup_map[t.setup_type].append(t)

    by_setup = []
    for st, ts in sorted(by_setup_map.items()):
        ws = [x for x in ts if x.outcome == "win"]
        ls = [x for x in ts if x.outcome == "loss"]
        sc = [x for x in ts if x.outcome == "scratch"]
        total_r_setup = sum(float(x.r_multiple or 0) for x in ts)
        by_setup.append(SetupBreakdown(
            setup_type=st,
            trades=len(ts),
            wins=len(ws),
            losses=len(ls),
            scratches=len(sc),
            win_rate=len(ws) / len(ts) if ts else 0.0,
            avg_r=total_r_setup / len(ts) if ts else 0.0,
            total_r=round(total_r_setup, 2),
        ))

    # By-month breakdown
    by_month_map: dict[str, list] = defaultdict(list)
    for t in tickets:
        dt = t.closed_at or t.filled_at or t.created_at
        if dt:
            key = dt.strftime("%Y-%m")
            by_month_map[key].append(t)

    by_month = []
    for month in sorted(by_month_map.keys()):
        ts = by_month_map[month]
        ws = [x for x in ts if x.outcome == "win"]
        total_r_m = sum(float(x.r_multiple or 0) for x in ts)
        by_month.append(MonthBreakdown(
            month=month,
            trades=len(ts),
            win_rate=len(ws) / len(ts) if ts else 0.0,
            avg_r=total_r_m / len(ts) if ts else 0.0,
            total_r=round(total_r_m, 2),
        ))

    # Equity curve (cumulative R, chronological)
    cumulative = 0.0
    equity_curve = []
    for t in tickets:
        r = float(t.r_multiple or 0)
        cumulative += r
        dt = t.closed_at or t.filled_at or t.created_at
        equity_curve.append({
            "date": dt.strftime("%Y-%m-%d") if dt else None,
            "symbol": t.symbol,
            "r": round(r, 2),
            "cumulative_r": round(cumulative, 2),
        })

    return JournalSummary(
        total_trades=n,
        wins=len(wins),
        losses=len(losses),
        scratches=len(scratches),
        win_rate=round(win_rate, 4),
        avg_r_winner=round(avg_winner, 3),
        avg_r_loser=round(avg_loser, 3),
        expectancy=round(expectancy, 3),
        profit_factor=round(profit_factor, 3),
        total_r=round(total_r, 2),
        total_realized_pnl=round(total_pnl, 2),
        by_setup=by_setup,
        by_month=by_month,
        equity_curve=equity_curve,
    )


# ── Broker-truth journal (from broker_trades, includes manual fills) ─────────


class BrokerJournalRow(BaseModel):
    symbol: str
    currency: str
    shares: float
    avg_entry_price: float
    avg_exit_price: float
    entry_date: str
    exit_date: str
    hold_days: int
    realized_pnl: float
    realized_pnl_pct: float | None
    r_multiple: float | None
    setup_type: str
    is_managed: bool          # has linked ticket
    ticket_id: str | None
    account_type: str | None


class BrokerJournalSummary(BaseModel):
    total_trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: float
    avg_pnl_winner: float    # mean $ P&L of winners (currency-mixed if cross-currency)
    avg_pnl_loser: float
    expectancy_dollars: float
    profit_factor: float
    total_realized_pnl_by_ccy: dict[str, float]
    avg_hold_days: float
    managed_count: int          # trades linked to tickets
    manual_count: int           # trades with no ticket — i.e. you placed them outside the app

    # R-multiple stats only for trades where we have a stop (linked ticket)
    r_trades_count: int
    avg_r: float
    total_r: float

    by_setup: list[SetupBreakdown]
    by_month: list[MonthBreakdown]
    equity_curve: list[dict]    # cumulative $ P&L (sum across currencies — note in UI)
    trades: list[BrokerJournalRow]


@router.get("/broker-summary", response_model=BrokerJournalSummary)
async def broker_summary(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> BrokerJournalSummary:
    """Journal computed from authoritative broker fills (BrokerTrade table).

    This is the source-of-truth view that includes every manual trade you placed
    at Questrade — not just the ones that came from in-app tickets. R-multiples
    are only populated for trades reconciled to a ticket that had a stop.
    """
    trades_q = await session.execute(
        select(BrokerTrade)
        .where(BrokerTrade.user_id == user_id)
        .order_by(BrokerTrade.exit_date.asc())
    )
    trades = trades_q.scalars().all()

    # Account-type lookup
    accounts_q = await session.execute(
        select(Account).where(Account.user_id == user_id)
    )
    acct_type_by_id = {a.id: a.type for a in accounts_q.scalars().all()}

    n = len(trades)
    if n == 0:
        return BrokerJournalSummary(
            total_trades=0, wins=0, losses=0, scratches=0,
            win_rate=0.0, avg_pnl_winner=0.0, avg_pnl_loser=0.0,
            expectancy_dollars=0.0, profit_factor=0.0,
            total_realized_pnl_by_ccy={}, avg_hold_days=0.0,
            managed_count=0, manual_count=0,
            r_trades_count=0, avg_r=0.0, total_r=0.0,
            by_setup=[], by_month=[], equity_curve=[], trades=[],
        )

    wins = [t for t in trades if t.realized_pnl > 0]
    losses = [t for t in trades if t.realized_pnl < 0]
    scratches = [t for t in trades if t.realized_pnl == 0]

    pnl_by_ccy: dict[str, float] = {}
    for t in trades:
        pnl_by_ccy[t.currency] = pnl_by_ccy.get(t.currency, 0.0) + float(t.realized_pnl)

    avg_winner = sum(float(t.realized_pnl) for t in wins) / len(wins) if wins else 0.0
    avg_loser  = sum(float(t.realized_pnl) for t in losses) / len(losses) if losses else 0.0
    win_rate   = len(wins) / n
    expectancy = win_rate * avg_winner + (1 - win_rate) * avg_loser

    gross_wins = sum(float(t.realized_pnl) for t in wins)
    gross_loss = abs(sum(float(t.realized_pnl) for t in losses))
    profit_factor = (gross_wins / gross_loss) if gross_loss > 0 else (float("inf") if gross_wins > 0 else 0.0)

    avg_hold = sum(t.hold_days for t in trades) / n if n else 0.0

    managed = sum(1 for t in trades if t.ticket_id is not None)
    manual = n - managed

    r_trades = [t for t in trades if t.r_multiple is not None]
    avg_r = (sum(float(t.r_multiple) for t in r_trades) / len(r_trades)) if r_trades else 0.0
    total_r = sum(float(t.r_multiple) for t in r_trades) if r_trades else 0.0

    # By setup
    from collections import defaultdict
    by_setup_map: dict[str, list[BrokerTrade]] = defaultdict(list)
    for t in trades:
        by_setup_map[t.setup_type or "manual"].append(t)
    by_setup: list[SetupBreakdown] = []
    for st, ts in sorted(by_setup_map.items()):
        s_wins = [x for x in ts if x.realized_pnl > 0]
        s_loss = [x for x in ts if x.realized_pnl < 0]
        s_sc   = [x for x in ts if x.realized_pnl == 0]
        r_vals = [float(x.r_multiple) for x in ts if x.r_multiple is not None]
        by_setup.append(SetupBreakdown(
            setup_type=st,
            trades=len(ts),
            wins=len(s_wins),
            losses=len(s_loss),
            scratches=len(s_sc),
            win_rate=len(s_wins) / len(ts) if ts else 0.0,
            avg_r=(sum(r_vals) / len(r_vals)) if r_vals else 0.0,
            total_r=round(sum(r_vals), 2),
        ))

    # By month
    by_month_map: dict[str, list[BrokerTrade]] = defaultdict(list)
    for t in trades:
        by_month_map[t.exit_date.strftime("%Y-%m")].append(t)
    by_month: list[MonthBreakdown] = []
    for month in sorted(by_month_map.keys()):
        ts = by_month_map[month]
        m_wins = [x for x in ts if x.realized_pnl > 0]
        r_vals = [float(x.r_multiple) for x in ts if x.r_multiple is not None]
        by_month.append(MonthBreakdown(
            month=month,
            trades=len(ts),
            win_rate=len(m_wins) / len(ts) if ts else 0.0,
            avg_r=(sum(r_vals) / len(r_vals)) if r_vals else 0.0,
            total_r=round(sum(r_vals), 2),
        ))

    # Equity curve in dollars (cross-currency sum — UI should warn if mixed)
    cumulative = 0.0
    equity_curve: list[dict] = []
    for t in trades:
        cumulative += float(t.realized_pnl)
        equity_curve.append({
            "date": t.exit_date.strftime("%Y-%m-%d"),
            "symbol": t.symbol,
            "pnl": round(float(t.realized_pnl), 2),
            "cumulative_pnl": round(cumulative, 2),
        })

    rows = [
        BrokerJournalRow(
            symbol=t.symbol,
            currency=t.currency,
            shares=float(t.shares),
            avg_entry_price=float(t.avg_entry_price),
            avg_exit_price=float(t.avg_exit_price),
            entry_date=t.entry_date.strftime("%Y-%m-%d"),
            exit_date=t.exit_date.strftime("%Y-%m-%d"),
            hold_days=t.hold_days,
            realized_pnl=float(t.realized_pnl),
            realized_pnl_pct=float(t.realized_pnl_pct) if t.realized_pnl_pct is not None else None,
            r_multiple=float(t.r_multiple) if t.r_multiple is not None else None,
            setup_type=t.setup_type or "manual",
            is_managed=t.ticket_id is not None,
            ticket_id=str(t.ticket_id) if t.ticket_id else None,
            account_type=acct_type_by_id.get(t.account_id),
        )
        for t in reversed(trades)   # newest first for UI
    ][:500]

    return BrokerJournalSummary(
        total_trades=n,
        wins=len(wins),
        losses=len(losses),
        scratches=len(scratches),
        win_rate=round(win_rate, 4),
        avg_pnl_winner=round(avg_winner, 2),
        avg_pnl_loser=round(avg_loser, 2),
        expectancy_dollars=round(expectancy, 2),
        profit_factor=round(profit_factor, 3) if profit_factor != float("inf") else 999.0,
        total_realized_pnl_by_ccy={k: round(v, 2) for k, v in pnl_by_ccy.items()},
        avg_hold_days=round(avg_hold, 1),
        managed_count=managed,
        manual_count=manual,
        r_trades_count=len(r_trades),
        avg_r=round(avg_r, 3),
        total_r=round(total_r, 2),
        by_setup=by_setup,
        by_month=by_month,
        equity_curve=equity_curve,
        trades=rows,
    )


# ── Behavioral coach ─────────────────────────────────────────────────────────

@router.get("/export.csv")
async def export_csv(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> StreamingResponse:
    """Download this user's closed trades as a CSV file."""
    result = await session.execute(
        select(Ticket)
        .where(
            Ticket.outcome.isnot(None),
            Ticket.user_id == user_id,
            Ticket.is_paper == False,  # noqa: E712
        )
        .order_by(Ticket.closed_at.asc().nullslast())
    )
    tickets = result.scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "symbol", "setup_type", "currency", "outcome", "r_multiple",
        "realized_pnl", "trigger_price", "stop_price", "target_price",
        "position_size_shares", "risk_pct", "risk_amount",
        "armed_at", "filled_at", "closed_at", "close_reason_tag", "thesis",
    ])
    for t in tickets:
        writer.writerow([
            t.symbol, t.setup_type, t.currency, t.outcome,
            float(t.r_multiple) if t.r_multiple else "",
            float(t.realized_pnl) if t.realized_pnl else "",
            float(t.trigger_price), float(t.stop_price),
            float(t.target_price) if t.target_price else "",
            t.position_size_shares,
            float(t.risk_pct), float(t.risk_amount),
            t.armed_at.isoformat() if t.armed_at else "",
            t.filled_at.isoformat() if t.filled_at else "",
            t.closed_at.isoformat() if t.closed_at else "",
            t.close_reason_tag or "",
            (t.thesis or "").replace("\n", " "),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trade-journal.csv"},
    )


class InsightOut(BaseModel):
    category: str
    severity: str
    headline: str
    detail: str
    data: dict


@router.get("/coach", response_model=list[InsightOut])
async def coach(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[InsightOut]:
    """Return behavioral insights based on your closed trade history."""
    insights = await compute_insights(session, user_id=user_id)
    return [InsightOut(
        category=i.category,
        severity=i.severity,
        headline=i.headline,
        detail=i.detail,
        data=i.data,
    ) for i in insights]
