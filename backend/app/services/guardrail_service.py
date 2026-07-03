"""Behavioral guardrails — enforced at ticket-creation time.

Rules:
  1. Regime gate          — block new tickets when market is in bear regime
                            (configurable: "warn" | "block" | "off")
  2. Max concurrent arms  — cap on simultaneously armed tickets (default 8)
  3. Loss-streak block    — after N consecutive losses, require explicit
                            override flag (default 3)
  4. Revenge-trade cooldown — block new tickets within X hours of a
                              stopped_out close (default 24h)
  5. Account drawdown breaker — tiered response to equity drawdown from peak
     (active-account scoped): warn at -10%, half-size at -12.5%, block new
     tickets at -15%. Override requires typed confirmation + audit entry.

Thresholds are configurable per user via the settings key
"{user_id}:guardrail_config" (JSON); code constants below are the defaults.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket, TicketStatus
from app.services.regime_service import RegimeResult
from app.services.settings_service import get_setting_json
from app.services.streak_service import get_snapshot

log = logging.getLogger(__name__)

# Defaults — per-user overrides live in the settings table.
MAX_CONCURRENT_ARMED   = 8
LOSS_STREAK_BLOCK_AT   = 3       # block at this many consecutive losses
REVENGE_COOLDOWN_HOURS = 24      # hours after a stop-out before new ticket
REGIME_GATE_MODE       = "warn"  # "off" | "warn" | "block"
DD_WARN_PCT            = 0.10    # warn at -10% from peak equity
DD_HALF_RISK_PCT       = 0.125   # halve per-trade risk at -12.5%
DD_BLOCK_PCT           = 0.15    # block new tickets at -15%

DRAWDOWN_OVERRIDE_PHRASE = "OVERRIDE DRAWDOWN BLOCK"


@dataclass(frozen=True)
class GuardrailConfig:
    max_armed: int = MAX_CONCURRENT_ARMED
    loss_streak_block: int = LOSS_STREAK_BLOCK_AT
    revenge_hours: int = REVENGE_COOLDOWN_HOURS
    regime_mode: str = REGIME_GATE_MODE
    dd_warn: float = DD_WARN_PCT
    dd_half_risk: float = DD_HALF_RISK_PCT
    dd_block: float = DD_BLOCK_PCT


async def load_guardrail_config(session: AsyncSession, user_id: str) -> GuardrailConfig:
    raw = await get_setting_json(session, f"{user_id}:guardrail_config") or {}
    defaults = GuardrailConfig()
    def _f(key: str, fallback: float) -> float:
        try:
            return float(raw.get(key, fallback))
        except (TypeError, ValueError):
            return fallback
    return GuardrailConfig(
        max_armed=int(_f("max_armed", defaults.max_armed)),
        loss_streak_block=int(_f("loss_streak_block", defaults.loss_streak_block)),
        revenge_hours=int(_f("revenge_hours", defaults.revenge_hours)),
        regime_mode=str(raw.get("regime_mode", defaults.regime_mode)),
        dd_warn=_f("dd_warn", defaults.dd_warn),
        dd_half_risk=_f("dd_half_risk", defaults.dd_half_risk),
        dd_block=_f("dd_block", defaults.dd_block),
    )


@dataclass(frozen=True)
class DrawdownState:
    """Account drawdown from peak, per the worst currency. tier: ok | warn |
    half_risk | block. risk_multiplier is applied on top of the streak
    multiplier when sizing new tickets."""
    peak_equity: Decimal
    current_equity: Decimal
    currency: str
    drawdown_pct: float          # positive number, e.g. 0.12 = down 12%
    tier: str
    risk_multiplier: Decimal
    has_history: bool            # False until the first equity snapshot exists


async def get_drawdown_state(
    session: AsyncSession,
    user_id: str,
    config: GuardrailConfig | None = None,
) -> DrawdownState:
    """Worst-currency drawdown for the active-account scope. No FX conversion
    (matches the app-wide per-currency stance) — the deepest per-currency
    drawdown governs."""
    from app.services.accounts_service import get_peak_and_current_equity

    if config is None:
        config = await load_guardrail_config(session, user_id)

    pairs = await get_peak_and_current_equity(session, user_id)
    worst: tuple[str, Decimal, Decimal, float] | None = None  # ccy, peak, cur, dd
    for currency, (peak, cur) in pairs.items():
        if peak <= 0:
            continue
        dd = float((peak - cur) / peak)
        if worst is None or dd > worst[3]:
            worst = (currency, peak, cur, dd)

    if worst is None:
        return DrawdownState(
            peak_equity=Decimal(0), current_equity=Decimal(0), currency="",
            drawdown_pct=0.0, tier="ok", risk_multiplier=Decimal("1.00"),
            has_history=False,
        )

    currency, peak, cur, dd = worst
    if dd >= config.dd_block:
        tier, mult = "block", Decimal("0.50")
    elif dd >= config.dd_half_risk:
        tier, mult = "half_risk", Decimal("0.50")
    elif dd >= config.dd_warn:
        tier, mult = "warn", Decimal("1.00")
    else:
        tier, mult = "ok", Decimal("1.00")

    return DrawdownState(
        peak_equity=peak, current_equity=cur, currency=currency,
        drawdown_pct=round(dd, 4), tier=tier, risk_multiplier=mult,
        has_history=True,
    )


class GuardrailViolation(Exception):
    """Raised when a guardrail blocks ticket creation."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GuardrailWarning:
    """Soft warning — ticket can still be created but user is informed."""
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message


async def check_all(
    session: AsyncSession,
    *,
    user_id: str,
    regime: RegimeResult | None = None,
    override_regime: bool = False,
    override_streak: bool = False,
    override_drawdown: bool = False,
) -> list[GuardrailWarning]:
    """Run all guardrails for one user. Raises GuardrailViolation on hard
    blocks. Returns a list of soft warnings (informational only).

    Every rule is scoped by user_id — one user's stop-out or armed tickets
    must never block another user. The API layer is responsible for verifying
    the typed confirmation phrase before passing override_drawdown=True.
    """
    warnings: list[GuardrailWarning] = []
    config = await load_guardrail_config(session, user_id)

    # 1. Regime gate
    if regime and regime.regime != "bull":
        if config.regime_mode == "block" and not override_regime:
            raise GuardrailViolation(
                "regime_bear",
                f"Market regime is '{regime.regime}' — new tickets are blocked. "
                "Wait for the broad market to reclaim its 200-day SMA, or pass override_regime=true.",
            )
        elif config.regime_mode == "warn" or (config.regime_mode == "block" and override_regime):
            warnings.append(GuardrailWarning(
                "regime_caution",
                f"Market regime is '{regime.regime}': {regime.message}",
            ))

    # 2. Max concurrent armed tickets
    armed_count_result = await session.execute(
        select(Ticket).where(
            Ticket.status == TicketStatus.ARMED.value,
            Ticket.user_id == user_id,
        )
    )
    armed = armed_count_result.scalars().all()
    if len(armed) >= config.max_armed:
        raise GuardrailViolation(
            "max_concurrent",
            f"You already have {len(armed)} armed tickets (limit: {config.max_armed}). "
            "Cancel or wait for existing tickets to trigger before adding more.",
        )

    # 3. Loss-streak block
    streak = await get_snapshot(session, user_id=user_id)
    if streak.consecutive_losses >= config.loss_streak_block and not override_streak:
        raise GuardrailViolation(
            "loss_streak",
            f"{streak.consecutive_losses} consecutive losses. "
            "Review your recent trades before adding new risk. "
            "Pass override_streak=true to proceed anyway.",
        )
    elif streak.consecutive_losses >= config.loss_streak_block - 1 and streak.consecutive_losses > 0:
        warnings.append(GuardrailWarning(
            "loss_streak_warning",
            f"{streak.consecutive_losses} consecutive loss(es) — cooldown approaching.",
        ))

    # 4. Revenge-trade cooldown
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.revenge_hours)
    recent_stopout = await session.execute(
        select(Ticket)
        .where(
            Ticket.status == TicketStatus.STOPPED_OUT.value,
            Ticket.closed_at >= cutoff,
            Ticket.user_id == user_id,
        )
        .order_by(Ticket.closed_at.desc())
        .limit(1)
    )
    recent = recent_stopout.scalar_one_or_none()
    if recent:
        hrs_ago = (datetime.now(timezone.utc) - recent.closed_at).total_seconds() / 3600
        raise GuardrailViolation(
            "revenge_trade",
            f"You were stopped out of {recent.symbol} {hrs_ago:.1f}h ago. "
            f"Wait {config.revenge_hours - hrs_ago:.1f} more hours before adding new risk. "
            "This prevents revenge trading.",
        )

    # 5. Account drawdown circuit breaker
    dd = await get_drawdown_state(session, user_id, config)
    if dd.has_history and dd.tier != "ok":
        dd_pct = dd.drawdown_pct * 100
        if dd.tier == "block" and not override_drawdown:
            raise GuardrailViolation(
                "drawdown_block",
                f"Account is down {dd_pct:.1f}% from its peak "
                f"({dd.currency} {float(dd.peak_equity):,.0f} → {float(dd.current_equity):,.0f}) "
                f"— beyond the {config.dd_block*100:.1f}% circuit breaker. New tickets are "
                f"blocked. Overriding requires typing '{DRAWDOWN_OVERRIDE_PHRASE}'.",
            )
        if dd.tier == "block" and override_drawdown:
            warnings.append(GuardrailWarning(
                "drawdown_overridden",
                f"Drawdown breaker OVERRIDDEN at -{dd_pct:.1f}% from peak. "
                "Sizing remains halved. This override is being audited.",
            ))
        elif dd.tier == "half_risk":
            warnings.append(GuardrailWarning(
                "drawdown_half_risk",
                f"Account is down {dd_pct:.1f}% from peak — per-trade risk is halved "
                f"until equity recovers above -{config.dd_half_risk*100:.1f}%.",
            ))
        elif dd.tier == "warn":
            warnings.append(GuardrailWarning(
                "drawdown_warning",
                f"Account is down {dd_pct:.1f}% from its peak. The breaker halves risk at "
                f"-{config.dd_half_risk*100:.1f}% and blocks new tickets at -{config.dd_block*100:.1f}%.",
            ))

    return warnings
