"""Trailing stop coaching — milestone detection and action queuing.

Minervini's trailing rules:
  +1R:  Move stop to breakeven (entry). Zero risk of loss.
  +2R:  Trail stop to entry + 0.5R. Lock in a small gain.
  +3R:  Move stop to entry + 1R. Let it run.
  +5R:  Sell 1/3 of position. Trail remainder to +2R stop.
  +10R: Sell another 1/3. Trail remainder with +2R stop.

`compute_trailing_suggestion` is called on-demand (ticket detail page).
`check_and_queue_actions` runs in the monitor loop — it detects when a
milestone is newly crossed and creates a `TrailingAction` record for the
user to confirm with one tap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Fill, Order, OrderIntent, Ticket, TrailingAction

log = logging.getLogger(__name__)


@dataclass
class TrailingSuggestion:
    open_r: float                   # current unrealised R (can be negative)
    new_stop: Decimal | None        # suggested new stop price
    action: str                     # human-readable action
    urgency: str                    # "info" | "warn" | "act"
    milestone_label: str            # e.g. "+2R milestone"


def compute_trailing_suggestion(
    entry_price: Decimal,
    stop_price: Decimal,
    current_price: Decimal,
    shares: int,
) -> TrailingSuggestion | None:
    """Return a trailing stop suggestion, or None if no action needed."""
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0 or current_price <= 0:
        return None

    gain = current_price - entry_price
    open_r = float(gain / per_share_risk)

    if open_r < -1.1:
        return None

    if open_r < 1.0:
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=None,
            action=f"Trade at {open_r:+.2f}R — no trailing action yet. Hold stop at {stop_price:.2f}.",
            urgency="info",
            milestone_label="Watching",
        )

    if open_r >= 10:
        new_stop = (entry_price + per_share_risk * 2).quantize(Decimal("0.01"))
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop,
            action=f"At +{open_r:.1f}R: sell 1/3, trail remainder stop to +2R ({float(new_stop):.2f}).",
            urgency="act",
            milestone_label="+10R milestone",
        )
    if open_r >= 5:
        new_stop = (entry_price + per_share_risk * 2).quantize(Decimal("0.01"))
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop,
            action=f"At +{open_r:.1f}R: sell 1/3. Trail stop to +2R ({float(new_stop):.2f}).",
            urgency="act",
            milestone_label="+5R milestone",
        )
    if open_r >= 3:
        new_stop = (entry_price + per_share_risk).quantize(Decimal("0.01"))
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop,
            action=f"At +{open_r:.1f}R: trail stop to +1R ({float(new_stop):.2f}).",
            urgency="warn",
            milestone_label="+3R milestone",
        )
    if open_r >= 2:
        new_stop = (entry_price + per_share_risk * Decimal("0.5")).quantize(Decimal("0.01"))
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop,
            action=f"At +{open_r:.1f}R: trail stop to +0.5R ({float(new_stop):.2f}). Lock in a gain.",
            urgency="warn",
            milestone_label="+2R milestone",
        )
    new_stop = entry_price.quantize(Decimal("0.01"))
    return TrailingSuggestion(
        open_r=round(open_r, 2),
        new_stop=new_stop,
        action=f"At +{open_r:.1f}R: move stop to breakeven ({float(new_stop):.2f}). Remove risk of loss.",
        urgency="warn",
        milestone_label="+1R milestone",
    )


def _milestone_for_r(open_r: float) -> str | None:
    """Return the highest actionable trailing milestone for this R-multiple."""
    if open_r >= 10: return "+10R"
    if open_r >= 5:  return "+5R"
    if open_r >= 3:  return "+3R"
    if open_r >= 2:  return "+2R"
    if open_r >= 1:  return "+1R"
    return None


async def _get_entry_price(session: AsyncSession, ticket: Ticket) -> Decimal:
    """Look up the actual fill price for this ticket's entry order."""
    result = await session.execute(
        select(Fill).join(Order).where(
            Order.ticket_id == ticket.id,
            Order.intent == OrderIntent.ENTRY.value,
            Fill.order_id == Order.id,
        ).order_by(Fill.occurred_at).limit(1)
    )
    fill = result.scalar_one_or_none()
    return fill.price if fill else ticket.trigger_price


async def check_and_queue_actions(
    session: AsyncSession,
    ticket: Ticket,
    current_price: Decimal,
) -> list[TrailingAction]:
    """Detect newly-crossed trailing milestones and exit ladder legs.

    Creates pending `TrailingAction` rows (idempotent — one per milestone per
    ticket). Returns the list of newly created actions so the caller can
    send notifications.

    Called from the monitor tick on every filled ticket that has a live quote.
    """
    entry_price = await _get_entry_price(session, ticket)
    per_share_risk = entry_price - ticket.stop_price
    if per_share_risk <= 0:
        return []

    gain = current_price - entry_price
    open_r = float(gain / per_share_risk)
    new_actions: list[TrailingAction] = []
    now = datetime.now(timezone.utc)

    # ── Trailing stop milestones ─────────────────────────────────────────────
    milestone = _milestone_for_r(open_r)
    if milestone:
        # Don't create another action if one already exists for this milestone
        existing = await session.execute(
            select(TrailingAction).where(
                TrailingAction.ticket_id == ticket.id,
                TrailingAction.milestone == milestone,
                TrailingAction.action_type == "trail_stop",
            )
        )
        if existing.scalar_one_or_none() is None:
            sugg = compute_trailing_suggestion(entry_price, ticket.stop_price, current_price, ticket.position_size_shares)
            if sugg and sugg.new_stop and sugg.new_stop != ticket.stop_price:
                action = TrailingAction(
                    ticket_id=ticket.id,
                    user_id=ticket.user_id,
                    action_type="trail_stop",
                    milestone=milestone,
                    old_stop=ticket.stop_price,
                    new_stop=sugg.new_stop,
                    open_r=Decimal(str(round(open_r, 2))),
                    triggered_price=current_price,
                    triggered_at=now,
                    status="pending",
                )
                session.add(action)
                new_actions.append(action)
                log.info("Trailing action queued: %s %s → %s (open_r=%.2f)",
                         ticket.symbol, milestone, sugg.new_stop, open_r)

    # ── Exit ladder legs ─────────────────────────────────────────────────────
    plan = ticket.exit_plan
    if plan and plan.get("targets"):
        for leg in plan["targets"]:
            if not leg.get("hit"):
                continue
            if leg.get("action_queued"):
                continue
            label = leg.get("label", "")
            # Check for duplicate
            ex2 = await session.execute(
                select(TrailingAction).where(
                    TrailingAction.ticket_id == ticket.id,
                    TrailingAction.milestone == label,
                    TrailingAction.action_type == "scale_out",
                )
            )
            if ex2.scalar_one_or_none() is not None:
                leg["action_queued"] = True
                continue
            action = TrailingAction(
                ticket_id=ticket.id,
                user_id=ticket.user_id,
                action_type="scale_out",
                milestone=label,
                sell_price=Decimal(str(leg["price"])),
                sell_shares=int(leg.get("shares", 0)),
                leg_label=label,
                open_r=Decimal(str(round(open_r, 2))),
                triggered_price=current_price,
                triggered_at=now,
                status="pending",
            )
            session.add(action)
            new_actions.append(action)
            leg["action_queued"] = True
            log.info("Scale-out action queued: %s %s %s sh @ %s",
                     ticket.symbol, label, leg.get("shares"), leg["price"])

        if any(leg.get("action_queued") for leg in plan["targets"]):
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(ticket, "exit_plan")

    return new_actions
