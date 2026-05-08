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

from app.db.models import Account, Fill, Order, OrderIntent, Ticket, TicketStatus
from app.db.session import get_session
from app.api.auth import get_user_id
from app.services.accounts_service import get_household_equity
from app.services.coach_service import Insight, compute_insights

router = APIRouter(prefix="/api/journal", tags=["journal"])


# ── Open risk dashboard ──────────────────────────────────────────────────────

class OpenPosition(BaseModel):
    symbol: str
    currency: str
    shares: int
    entry_price: Decimal | None
    stop_price: Decimal
    open_risk_dollars: Decimal   # (entry - stop) × shares
    open_r_multiple: Decimal | None  # current unrealised R (needs live quote — approximated)
    sector: str | None
    account_type: str
    is_paper: bool


class OpenRiskSummary(BaseModel):
    positions: list[OpenPosition]
    total_risk_usd: Decimal
    total_risk_cad: Decimal
    total_equity_usd: Decimal
    total_equity_cad: Decimal
    risk_pct_usd: Decimal    # as fraction
    risk_pct_cad: Decimal
    max_risk_pct: Decimal    # configured cap (8%)
    warning: str | None      # non-null if approaching or exceeding cap


@router.get("/risk", response_model=OpenRiskSummary)
async def open_risk(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> OpenRiskSummary:
    """Aggregate current open risk across all filled (active) tickets for this user."""
    MAX_RISK_PCT = Decimal("0.08")

    filled_result = await session.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.FILLED.value,
            Ticket.user_id == user_id,
        )
    )
    filled_tickets = filled_result.scalars().all()

    equity = await get_household_equity(session, user_id=user_id)
    total_usd = equity.get("USD", Decimal(0))
    total_cad = equity.get("CAD", Decimal(0))

    positions: list[OpenPosition] = []
    total_risk_usd = Decimal(0)
    total_risk_cad = Decimal(0)

    for t in filled_tickets:
        # Get entry fill price if available
        fill_result = await session.execute(
            select(Fill).join(Order).where(
                Order.ticket_id == t.id,
                Order.intent == OrderIntent.ENTRY.value,
            ).order_by(Fill.occurred_at).limit(1)
        )
        fill = fill_result.scalar_one_or_none()
        entry_price = fill.price if fill else None

        per_share_risk = (entry_price - t.stop_price) if entry_price else (t.trigger_price - t.stop_price)
        open_risk_dollars = per_share_risk * t.position_size_shares

        account = await session.get(Account, t.account_id)
        account_type = account.type if account else "Unknown"

        pos = OpenPosition(
            symbol=t.symbol,
            currency=t.currency,
            shares=t.position_size_shares,
            entry_price=entry_price,
            stop_price=t.stop_price,
            open_risk_dollars=open_risk_dollars.quantize(Decimal("0.01")),
            open_r_multiple=None,
            sector=None,
            account_type=account_type,
            is_paper=t.is_paper,
        )
        positions.append(pos)

        if t.currency == "USD":
            total_risk_usd += open_risk_dollars
        else:
            total_risk_cad += open_risk_dollars

    risk_pct_usd = (total_risk_usd / total_usd).quantize(Decimal("0.0001")) if total_usd > 0 else Decimal(0)
    risk_pct_cad = (total_risk_cad / total_cad).quantize(Decimal("0.0001")) if total_cad > 0 else Decimal(0)

    warning = None
    max_pct = max(risk_pct_usd, risk_pct_cad)
    if max_pct >= MAX_RISK_PCT:
        warning = f"Open risk ({float(max_pct)*100:.1f}%) is at or above the 8% cap. Do not add new positions."
    elif max_pct >= MAX_RISK_PCT * Decimal("0.75"):
        warning = f"Open risk ({float(max_pct)*100:.1f}%) approaching 8% cap. Be selective with new entries."

    return OpenRiskSummary(
        positions=positions,
        total_risk_usd=total_risk_usd.quantize(Decimal("0.01")),
        total_risk_cad=total_risk_cad.quantize(Decimal("0.01")),
        total_equity_usd=total_usd,
        total_equity_cad=total_cad,
        risk_pct_usd=risk_pct_usd,
        risk_pct_cad=risk_pct_cad,
        max_risk_pct=MAX_RISK_PCT,
        warning=warning,
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


# ── Behavioral coach ─────────────────────────────────────────────────────────

@router.get("/export.csv")
async def export_csv(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> StreamingResponse:
    """Download this user's closed trades as a CSV file."""
    result = await session.execute(
        select(Ticket)
        .where(Ticket.outcome.isnot(None), Ticket.user_id == user_id)
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
