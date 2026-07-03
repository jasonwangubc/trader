"""Charter versions (append-only) + honesty-page counterfactual math."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db.models import AccountCashFlow, DailyBar, EquitySnapshot
from app.services.charter_service import (
    compute_performance,
    create_charter_version,
    get_active_charter,
    list_charter_versions,
)
from tests.factories import make_account

USER = "charter_user"
CONTENT_V1 = "# My Trading Charter\n" + "Risk 0.75% per trade, stop honored always. " * 3


async def test_versions_are_append_only(db_session):
    v1 = await create_charter_version(
        db_session, user_id=USER, content_md=CONTENT_V1,
        rules={"dd_block": 0.15, "horizon_years": 2},
    )
    assert v1.version == 1

    v2 = await create_charter_version(
        db_session, user_id=USER, content_md=CONTENT_V1 + "\nLoosened nothing.",
        rules={"dd_block": 0.15, "horizon_years": 2},
        note="clarified wording",
    )
    assert v2.version == 2

    versions = await list_charter_versions(db_session, USER)
    assert [v.version for v in versions] == [2, 1]
    # v1 content untouched.
    assert versions[1].content_md == CONTENT_V1

    active = await get_active_charter(db_session, USER)
    assert active is not None and active.version == 2


def _bar(symbol: str, day: datetime, price: float) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        bar_date=day,
        open=Decimal(str(price)),
        high=Decimal(str(price)),
        low=Decimal(str(price)),
        close=Decimal(str(price)),
        adj_close=Decimal(str(price)),
        volume=1_000,
    )


async def test_counterfactual_hand_computed(db_session):
    """Two deposits and one withdrawal replayed into a benchmark:

      day 0  price 100  deposit 10,000  -> 100 units
      day 10 price 110  deposit 11,000  -> +100 units (200 total)
      day 20 price 100  withdraw 5,000  -> -50 units (150 total)
      day 30 price 120  -> counterfactual 18,000
    """
    account = await make_account(db_session, user_id=USER, currency="USD")
    base = datetime(2026, 1, 5, tzinfo=timezone.utc)

    # 31 daily benchmark bars with a deterministic price path.
    prices = {0: 100.0, 10: 110.0, 20: 100.0, 30: 120.0}
    last = 100.0
    for i in range(31):
        last = prices.get(i, last)
        db_session.add(_bar("SPY", datetime(2026, 1, 5) + timedelta(days=i), last))

    flows = [
        (0, "10000", "deposit"),
        (10, "11000", "deposit"),
        (20, "-5000", "withdrawal"),
    ]
    for day, amount, ftype in flows:
        db_session.add(AccountCashFlow(
            user_id=USER,
            account_id=account.id,
            broker_activity_id=f"cf_test_{day}",
            flow_type=ftype,
            currency="USD",
            amount=Decimal(amount),
            occurred_at=base + timedelta(days=day),
        ))

    # Actual equity snapshots for the last few days (shorter than the flows —
    # the honest asymmetry).
    for i in (28, 29, 30):
        db_session.add(EquitySnapshot(
            user_id=USER,
            account_id=account.id,
            currency="USD",
            snapshot_date=datetime(2026, 1, 5) + timedelta(days=i),
            total_equity=Decimal("17000"),
            market_value=Decimal("17000"),
            source="sync",
        ))
    await db_session.commit()

    results = await compute_performance(db_session, USER)
    (perf,) = [r for r in results if r.currency == "USD"]

    assert perf.benchmark_symbol == "SPY"
    assert perf.flow_count == 3
    assert perf.deposits_total == 21000.0
    assert perf.withdrawals_total == -5000.0

    by_date = {p.date: p for p in perf.points}
    assert by_date["2026-01-05"].counterfactual == 10000.0            # 100u x 100
    assert by_date["2026-01-15"].counterfactual == 22000.0            # 200u x 110
    assert by_date["2026-01-25"].counterfactual == 15000.0            # 150u x 100
    assert by_date["2026-02-04"].counterfactual == 18000.0            # 150u x 120

    # Actual line only exists where snapshots exist.
    assert by_date["2026-01-15"].actual is None
    assert by_date["2026-02-04"].actual == 17000.0

    # 3 days of overlap -> not enough history for a verdict.
    assert perf.status == "insufficient_history"
    assert perf.latest_actual == 17000.0
    assert perf.latest_counterfactual == 18000.0


async def test_performance_scopes_to_active_account(db_session):
    from app.services.accounts_service import set_active_account_id

    rrsp = await make_account(db_session, user_id=USER, account_type="RRSP", currency="USD")
    resp = await make_account(db_session, user_id=USER, account_type="RESP", currency="USD")
    db_session.add(_bar("SPY", datetime(2026, 1, 5), 100.0))
    for account, dedup in ((rrsp, "a"), (resp, "b")):
        db_session.add(AccountCashFlow(
            user_id=USER,
            account_id=account.id,
            broker_activity_id=f"cf_{dedup}",
            flow_type="deposit",
            currency="USD",
            amount=Decimal("1000"),
            occurred_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
        ))
    await db_session.commit()

    await set_active_account_id(db_session, USER, rrsp.id)
    results = await compute_performance(db_session, USER)
    (perf,) = [r for r in results if r.currency == "USD"]
    assert perf.flow_count == 1          # RESP deposit invisible
    assert perf.deposits_total == 1000.0
