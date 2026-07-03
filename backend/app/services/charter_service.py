"""Trading charter (pre-commitment) + honesty-page performance math.

Three concerns:
  1. Charter CRUD — append-only versions. Rules are written before the
     drawdown; revisions create new versions with an audit trail, never edits.
  2. Cash-flow sync — deposits/withdrawals/transfers from the broker's
     activities feed (backfillable ~2 years, unlike equity history).
  3. compute_performance — the honesty page: actual equity curve (from
     EquitySnapshot, active-account scoped) vs a deposit-timing-matched
     buy-and-hold counterfactual (CAD flows buy ZSP.TO, USD flows buy SPY,
     at each flow date's adjusted close).

Honesty constraints the UI must render:
  • Equity history builds forward from the first snapshot (M5); the
    benchmark line will span years of cash-flow history while the actual
    line starts recently. That asymmetry is real — show it, don't fake it.
  • No FX conversion — per-currency curves, consistent with the app.
  • Transfers count as flows: at single-account scope they are real money
    entering/leaving the experiment; at household scope both sides appear
    and cancel naturally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountCashFlow, CharterVersion, EquitySnapshot
from app.services.audit_service import log_event

log = logging.getLogger(__name__)

CHUNK_DAYS = 30
DEFAULT_BACKFILL_YEARS = 2

# Benchmark per currency: the realistic "just index it" alternative.
BENCHMARK_BY_CURRENCY = {"USD": "SPY", "CAD": "ZSP.TO"}
# Minimum days of actual history before the kill-criteria check is meaningful.
MIN_HISTORY_DAYS = 180


# ─── Charter CRUD (append-only) ───────────────────────────────────────────────

async def get_active_charter(session: AsyncSession, user_id: str) -> CharterVersion | None:
    q = await session.execute(
        select(CharterVersion)
        .where(CharterVersion.user_id == user_id)
        .order_by(CharterVersion.version.desc())
        .limit(1)
    )
    return q.scalar_one_or_none()


async def list_charter_versions(session: AsyncSession, user_id: str) -> list[CharterVersion]:
    q = await session.execute(
        select(CharterVersion)
        .where(CharterVersion.user_id == user_id)
        .order_by(CharterVersion.version.desc())
    )
    return list(q.scalars().all())


async def create_charter_version(
    session: AsyncSession,
    *,
    user_id: str,
    content_md: str,
    rules: dict | None = None,
    note: str | None = None,
) -> CharterVersion:
    """Append a new charter version. Prior versions are never touched."""
    current = await get_active_charter(session, user_id)
    version = (current.version + 1) if current else 1
    row = CharterVersion(
        user_id=user_id,
        version=version,
        content_md=content_md,
        rules=rules or {},
        note=note,
    )
    session.add(row)
    await session.flush()
    await log_event(
        session,
        actor="user",
        event_type="charter_revised" if version > 1 else "charter_created",
        entity_type="charter",
        entity_id=row.id,
        payload={"version": version, "note": note},
    )
    await session.commit()
    return row


# ─── Cash-flow sync ───────────────────────────────────────────────────────────

async def sync_cash_flows_for_user(
    session: AsyncSession,
    *,
    user_id: str,
    backfill_years: int = DEFAULT_BACKFILL_YEARS,
) -> int:
    """Pull deposits/withdrawals/transfers for every active account.

    Incremental: starts a week before the newest stored flow (late postings),
    else `backfill_years` back. Idempotent via ON CONFLICT DO NOTHING on the
    synthetic activity id. Returns rows inserted.
    """
    from app.brokers.registry import get_broker

    accounts_q = await session.execute(
        select(Account).where(Account.user_id == user_id, Account.is_active == True)  # noqa: E712
    )
    accounts = accounts_q.scalars().all()
    if not accounts:
        return 0

    broker = get_broker(user_id=user_id)
    now = datetime.now(timezone.utc)
    inserted = 0

    for account in accounts:
        latest_q = await session.execute(
            select(AccountCashFlow.occurred_at)
            .where(AccountCashFlow.account_id == account.id)
            .order_by(AccountCashFlow.occurred_at.desc())
            .limit(1)
        )
        latest = latest_q.scalar_one_or_none()
        start = (latest - timedelta(days=7)) if latest else now - timedelta(days=365 * backfill_years)

        cur = start
        while cur < now:
            nxt = min(cur + timedelta(days=CHUNK_DAYS), now)
            try:
                flows = await broker.get_cash_activities(account.questrade_account_id, cur, nxt)
            except Exception as exc:
                log.warning("Cash-flow fetch failed for %s %s-%s: %s",
                            account.questrade_account_id, cur, nxt, exc)
                break
            if flows:
                rows = [
                    {
                        "user_id": user_id,
                        "account_id": account.id,
                        "broker_activity_id": f.broker_activity_id,
                        "flow_type": f.flow_type,
                        "currency": f.currency,
                        "amount": f.amount,
                        "occurred_at": f.occurred_at,
                        "description": f.description,
                        "raw": f.raw,
                    }
                    for f in flows
                ]
                stmt = (
                    pg_insert(AccountCashFlow.__table__)
                    .values(rows)
                    .on_conflict_do_nothing(constraint="uq_cash_flow_user_activity")
                )
                result = await session.execute(stmt)
                inserted += result.rowcount or 0
                await session.commit()
            cur = nxt

    return inserted


# ─── Performance / honesty page ───────────────────────────────────────────────

@dataclass
class PerfPoint:
    date: str                       # YYYY-MM-DD
    counterfactual: float | None    # benchmark buy-and-hold value
    actual: float | None            # snapshot equity (starts later — honest gap)


@dataclass
class MonthRow:
    month: str                      # YYYY-MM
    actual_end: float | None
    counterfactual_end: float | None


@dataclass
class CurrencyPerformance:
    currency: str
    benchmark_symbol: str
    deposits_total: float
    withdrawals_total: float
    flow_count: int
    points: list[PerfPoint] = field(default_factory=list)
    monthly: list[MonthRow] = field(default_factory=list)
    actual_max_drawdown_pct: float | None = None
    latest_actual: float | None = None
    latest_counterfactual: float | None = None
    # ok | lagging | insufficient_history — the charter's kill-criteria signal
    status: str = "insufficient_history"
    status_detail: str = ""


async def _benchmark_bars(session: AsyncSession, symbol: str):
    """Adjusted-close bars for a benchmark, fetching on demand if missing."""
    from app.services.eod_service import get_bars_df, sync_eod_incremental

    df = await get_bars_df(session, symbol, days=800)
    if df.empty or len(df) < 30:
        try:
            await sync_eod_incremental(session, [symbol], full_years=3, delta_days=35)
        except Exception:
            log.exception("Benchmark fetch failed for %s", symbol)
        df = await get_bars_df(session, symbol, days=800)
    return df


async def compute_performance(
    session: AsyncSession,
    user_id: str,
) -> list[CurrencyPerformance]:
    """Per-currency actual-vs-counterfactual curves, active-account scoped."""
    from app.services.accounts_service import get_active_account_id

    account_id = await get_active_account_id(session, user_id)

    flow_stmt = select(AccountCashFlow).where(
        AccountCashFlow.user_id == user_id
    ).order_by(AccountCashFlow.occurred_at.asc())
    if account_id is not None:
        flow_stmt = flow_stmt.where(AccountCashFlow.account_id == account_id)
    flows = list((await session.execute(flow_stmt)).scalars().all())

    snap_stmt = select(EquitySnapshot).where(
        EquitySnapshot.user_id == user_id
    ).order_by(EquitySnapshot.snapshot_date.asc())
    if account_id is not None:
        snap_stmt = snap_stmt.where(EquitySnapshot.account_id == account_id)
    snapshots = list((await session.execute(snap_stmt)).scalars().all())

    currencies = sorted({f.currency for f in flows} | {s.currency for s in snapshots})
    out: list[CurrencyPerformance] = []

    for currency in currencies:
        benchmark = BENCHMARK_BY_CURRENCY.get(currency)
        ccy_flows = [f for f in flows if f.currency == currency]
        ccy_snaps = [s for s in snapshots if s.currency == currency]

        perf = CurrencyPerformance(
            currency=currency,
            benchmark_symbol=benchmark or "",
            deposits_total=float(sum((f.amount for f in ccy_flows if f.amount > 0), Decimal(0))),
            withdrawals_total=float(sum((f.amount for f in ccy_flows if f.amount < 0), Decimal(0))),
            flow_count=len(ccy_flows),
        )

        # Actual equity by day (sum across accounts in scope).
        actual_by_date: dict[str, float] = {}
        for s in ccy_snaps:
            d = s.snapshot_date.strftime("%Y-%m-%d")
            actual_by_date[d] = actual_by_date.get(d, 0.0) + float(s.total_equity)

        # Counterfactual: replay flows into benchmark units.
        points: list[PerfPoint] = []
        if benchmark and ccy_flows:
            bars = await _benchmark_bars(session, benchmark)
            if not bars.empty:
                dates = [
                    (d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10])
                    for d in bars["date"].tolist()
                ]
                closes = [float(c) for c in bars["adj_close"].tolist()]
                units = 0.0
                flow_idx = 0
                first_flow_date = ccy_flows[0].occurred_at.strftime("%Y-%m-%d")
                for d, close in zip(dates, closes):
                    # Apply every flow dated on/before this bar.
                    while flow_idx < len(ccy_flows) and \
                            ccy_flows[flow_idx].occurred_at.strftime("%Y-%m-%d") <= d:
                        if close > 0:
                            units += float(ccy_flows[flow_idx].amount) / close
                        flow_idx += 1
                    if d < first_flow_date:
                        continue
                    points.append(PerfPoint(
                        date=d,
                        counterfactual=round(units * close, 2),
                        actual=actual_by_date.get(d),
                    ))
        elif actual_by_date:
            # No benchmark/flows — actual-only series so the page still works.
            points = [
                PerfPoint(date=d, counterfactual=None, actual=v)
                for d, v in sorted(actual_by_date.items())
            ]
        perf.points = points

        # Monthly table: last value per month for each line.
        monthly: dict[str, MonthRow] = {}
        for p in points:
            month = p.date[:7]
            row = monthly.setdefault(month, MonthRow(month=month, actual_end=None, counterfactual_end=None))
            if p.counterfactual is not None:
                row.counterfactual_end = p.counterfactual
            if p.actual is not None:
                row.actual_end = p.actual
        perf.monthly = [monthly[m] for m in sorted(monthly)]

        # Max drawdown of the actual curve (peak-walk).
        peak = 0.0
        max_dd = 0.0
        for d in sorted(actual_by_date):
            v = actual_by_date[d]
            peak = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)
        perf.actual_max_drawdown_pct = round(max_dd, 4) if actual_by_date else None

        # Kill-criteria status: compare growth vs counterfactual over the
        # common window once enough actual history exists.
        actual_dates = sorted(actual_by_date)
        cf_by_date = {p.date: p.counterfactual for p in points if p.counterfactual is not None}
        common = [d for d in actual_dates if d in cf_by_date]
        if common:
            perf.latest_actual = actual_by_date[common[-1]]
            perf.latest_counterfactual = cf_by_date[common[-1]]
        elif actual_dates:
            perf.latest_actual = actual_by_date[actual_dates[-1]]

        if len(common) >= 2:
            first, last = common[0], common[-1]
            span_days = (
                datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")
            ).days
            if span_days < MIN_HISTORY_DAYS:
                perf.status = "insufficient_history"
                perf.status_detail = (
                    f"Only {span_days} days of overlapping history — the vs-benchmark "
                    f"verdict needs at least {MIN_HISTORY_DAYS}."
                )
            else:
                a0, a1 = actual_by_date[first], actual_by_date[last]
                c0, c1 = cf_by_date[first], cf_by_date[last]
                a_growth = (a1 / a0 - 1) if a0 > 0 else 0.0
                c_growth = (c1 / c0 - 1) if c0 and c0 > 0 else 0.0
                perf.status = "ok" if a_growth >= c_growth else "lagging"
                perf.status_detail = (
                    f"Over {span_days} days: account {a_growth * 100:+.1f}% vs "
                    f"{perf.benchmark_symbol} buy-and-hold {c_growth * 100:+.1f}%."
                )
        else:
            perf.status = "insufficient_history"
            if not perf.status_detail:
                perf.status_detail = (
                    "Equity snapshots start accruing from the first account sync; "
                    "the benchmark line spans the full cash-flow history."
                )

        out.append(perf)

    return out
