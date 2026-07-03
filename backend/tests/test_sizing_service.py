"""Position-sizing math — pure function, no DB.

Explicit base/max risk percentages are passed everywhere so results don't
depend on the developer's .env.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.sizing_service import compute_sizing

BASE = Decimal("0.01")   # 1% base risk
CAP = Decimal("0.02")    # 2% cap


def _size(**overrides):
    kwargs = dict(
        trigger_price=Decimal("100"),
        stop_price=Decimal("95"),
        currency="CAD",
        equity_by_currency={"CAD": Decimal("50000")},
        multiplier=Decimal("1.00"),
        base_risk_pct=BASE,
        max_risk_pct=CAP,
    )
    kwargs.update(overrides)
    return compute_sizing(**kwargs)


def test_basic_share_math():
    # 1% of 50k = $500 risk; $5/share risk -> 100 shares.
    result = _size()
    assert result.shares == 100
    assert result.risk_amount == Decimal("500.00")
    assert result.per_share_risk == Decimal("5.0000")
    assert result.position_value == Decimal("10000.00")
    assert result.risk_pct == Decimal("0.01000")
    assert not result.capped


def test_shares_floor_not_round():
    # $500 / $5.30 = 94.33... -> 94 shares, never rounded up.
    result = _size(stop_price=Decimal("94.70"))
    assert result.shares == 94


def test_multiplier_scales_risk():
    result = _size(multiplier=Decimal("1.50"))
    assert result.shares == 150  # 1.5% of 50k = $750 / $5


def test_multiplier_capped_at_max_risk():
    # 1% x 2.5 = 2.5% -> capped to 2%.
    result = _size(multiplier=Decimal("2.50"))
    assert result.capped
    assert result.shares == 200  # 2% of 50k = $1000 / $5


def test_zero_equity_returns_zero_shares_with_warning():
    result = _size(equity_by_currency={"CAD": Decimal("0")})
    assert result.shares == 0
    assert any("No CAD equity" in w for w in result.warnings)


def test_missing_currency_treated_as_zero_equity():
    result = _size(equity_by_currency={"USD": Decimal("50000")})
    assert result.shares == 0
    assert any("No CAD equity" in w for w in result.warnings)


def test_stop_at_or_above_trigger_returns_zero_shares():
    for stop in (Decimal("100"), Decimal("101")):
        result = _size(stop_price=stop)
        assert result.shares == 0
        assert any("Stop must be below trigger" in w for w in result.warnings)


def test_max_shares_cap_recomputes_actual_risk():
    # Risk-based sizing would be 100 shares; cap to 40 and the reported
    # risk must reflect the 40 shares actually taken, not the original $500.
    result = _size(max_shares=40)
    assert result.shares == 40
    assert result.risk_amount == Decimal("200.00")          # 40 x $5
    assert result.risk_pct == Decimal("0.00400")             # 200 / 50000
    assert any("Capped to 40 shares" in w for w in result.warnings)


def test_tight_stop_warns_of_whipsaw():
    # Stop 0.5% below trigger -> whipsaw warning, but sizing still works.
    result = _size(stop_price=Decimal("99.50"))
    assert result.shares == 1000  # $500 / $0.50
    assert any("whipsaw" in w for w in result.warnings)


def test_risk_amount_too_small_for_one_share():
    # $2 risk budget, $5 per-share risk -> 0 shares with explanation.
    result = _size(equity_by_currency={"CAD": Decimal("200")})
    assert result.shares == 0
    assert any("too small" in w for w in result.warnings)
