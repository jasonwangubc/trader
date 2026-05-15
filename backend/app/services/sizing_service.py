"""Position sizing — pure function. Given trigger, stop, currency, and equity
state, return the maximum share count that respects the streak-scaled risk %.

Sizing rules (MVP):
- Risk % = base × streak multiplier, capped at MAX_RISK_PCT.
- Equity basis = total household equity in the trade's currency. We do NOT
  cross-convert FX in MVP — a USD trade sizes off USD equity, a CAD trade
  off CAD equity. This avoids FX volatility on sizing and matches Questrade's
  per-currency buying-power model. Revisit when we add FX support.
- Per-share risk = trigger - stop (long-only for now). If <= 0, sizing fails.
- Shares = floor(risk_amount / per_share_risk). Always integer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN

from app.config import get_settings


@dataclass(frozen=True)
class SizingResult:
    risk_pct: Decimal           # effective % applied (base × multiplier, capped)
    base_risk_pct: Decimal
    multiplier: Decimal
    capped: bool
    equity_basis: Decimal       # equity in trade currency used for risk calc
    equity_currency: str
    risk_amount: Decimal        # in trade currency
    per_share_risk: Decimal     # trigger - stop
    shares: int
    position_value: Decimal
    warnings: list[str] = field(default_factory=list)


def compute_sizing(
    *,
    trigger_price: Decimal,
    stop_price: Decimal,
    currency: str,
    equity_by_currency: dict[str, Decimal],
    multiplier: Decimal,
    base_risk_pct: Decimal | None = None,
    max_risk_pct: Decimal | None = None,
    max_shares: int | None = None,
) -> SizingResult:
    settings = get_settings()
    base = base_risk_pct if base_risk_pct is not None else Decimal(str(settings.base_risk_pct))
    cap = max_risk_pct if max_risk_pct is not None else Decimal(str(settings.max_risk_pct))

    raw = base * multiplier
    capped = raw > cap
    risk_pct = cap if capped else raw

    equity = equity_by_currency.get(currency, Decimal(0))
    risk_amount = (equity * risk_pct).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    per_share_risk = (trigger_price - stop_price).quantize(Decimal("0.0001"))
    warnings: list[str] = []

    if equity <= 0:
        warnings.append(f"No {currency} equity available — sizing returns 0 shares.")
    if per_share_risk <= 0:
        warnings.append("Stop must be below trigger (long entry); sizing returns 0 shares.")
        shares = 0
    else:
        shares_decimal = (risk_amount / per_share_risk).quantize(Decimal("1"), rounding=ROUND_DOWN)
        shares = max(int(shares_decimal), 0)

    if shares == 0 and not warnings:
        warnings.append("Risk amount too small for any whole shares — try widening stop or re-check risk %.")

    if max_shares is not None and max_shares > 0 and shares > max_shares:
        warnings.append(f"Capped to {max_shares} shares (risk-based sizing was {shares}).")
        shares = max_shares

    # Tight-stop warning: if risk per share is < 1% of trigger, the breakout might be too tight.
    if per_share_risk > 0 and per_share_risk / trigger_price < Decimal("0.01"):
        warnings.append("Stop is <1% from trigger — risk of whipsaw on noise.")

    position_value = (Decimal(shares) * trigger_price).quantize(Decimal("0.01"))
    # Recalculate actual risk after any share cap so preview shows real exposure.
    actual_risk_amount = (Decimal(shares) * per_share_risk).quantize(Decimal("0.01")) if per_share_risk > 0 else Decimal(0)
    actual_risk_pct = (actual_risk_amount / equity).quantize(Decimal("0.00001")) if equity > 0 else Decimal(0)

    return SizingResult(
        risk_pct=actual_risk_pct,
        base_risk_pct=base,
        multiplier=multiplier,
        capped=capped,
        equity_basis=equity,
        equity_currency=currency,
        risk_amount=actual_risk_amount,
        per_share_risk=per_share_risk,
        shares=shares,
        position_value=position_value,
        warnings=warnings,
    )
