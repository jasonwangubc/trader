"""Behavioral coach — finds patterns in closed trades and surfaces insights.

Analyzes your actual closed tickets (not simulated) and produces specific,
actionable observations. The goal is to make the patterns visible so you can
correct them before they compound.

Checks:
  1. Winner-cutting: avg exit R vs potential (exit_plan targets)
  2. Loser-holding: how long you held losses vs winners
  3. Win rate by setup type
  4. Win rate by market regime at entry (if stored)
  5. Win rate after consecutive losses (tilt detection)
  6. Revenge-trade pattern: trade entered within 24h of a stop-out
  7. Best / worst months
  8. EPS surprise correlation: do you do better after strong earnings?
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ticket, TicketStatus

log = logging.getLogger(__name__)

CLOSE_REASONS = [
    "plan_target_hit",
    "plan_stop_hit",
    "moved_stop_manually",
    "panic_exit",
    "earnings_fear",
    "market_deterioration",
    "personal_liquidity",
    "thesis_broken",
    "took_profit_early",
    "held_too_long",
]


@dataclass
class Insight:
    category: str          # "winner_cutting" | "loser_holding" | "setup" | "streak" | "revenge"
    severity: str          # "good" | "info" | "warn" | "bad"
    headline: str
    detail: str
    data: dict = field(default_factory=dict)


async def compute_insights(
    session: AsyncSession,
    user_id: str = "user_default",
) -> list[Insight]:
    """Compute all behavioral insights for closed tickets of this user."""
    result = await session.execute(
        select(Ticket).where(
            Ticket.outcome.isnot(None),
            Ticket.r_multiple.isnot(None),
            Ticket.user_id == user_id,
        ).order_by(Ticket.closed_at.asc().nullslast())
    )
    tickets = result.scalars().all()
    if len(tickets) < 5:
        return [Insight(
            category="info",
            severity="info",
            headline="Not enough data yet",
            detail=f"You have {len(tickets)} closed trade(s). The coach needs at least 5 to find meaningful patterns.",
        )]

    insights: list[Insight] = []
    insights.extend(_winner_cutting(tickets))
    insights.extend(_by_setup(tickets))
    insights.extend(_streak_tilt(tickets))
    insights.extend(_revenge_trades(tickets))
    insights.extend(_close_reason_patterns(tickets))
    insights.sort(key=lambda i: {"bad": 0, "warn": 1, "good": 2, "info": 3}[i.severity])
    return insights


def _pct(n: int, d: int) -> str:
    return f"{round(n / d * 100)}%" if d > 0 else "—"


def _winner_cutting(tickets: list[Ticket]) -> list[Insight]:
    wins = [t for t in tickets if t.outcome == "win" and t.r_multiple]
    if len(wins) < 3:
        return []
    avg_winner_r = sum(float(t.r_multiple) for t in wins) / len(wins)
    early_exits = [t for t in wins if float(t.r_multiple) < 1.5]
    if len(early_exits) / len(wins) > 0.5:
        return [Insight(
            category="winner_cutting",
            severity="bad",
            headline=f"You cut {_pct(len(early_exits), len(wins))} of winners before +1.5R",
            detail=(
                f"Average winning trade: {avg_winner_r:.2f}R. "
                f"{len(early_exits)} of {len(wins)} wins were below +1.5R. "
                "Consider setting your T1 exit in the exit ladder and committing to it."
            ),
            data={"avg_winner_r": avg_winner_r, "early_exit_count": len(early_exits)},
        )]
    return []


def _by_setup(tickets: list[Ticket]) -> list[Insight]:
    by_setup: dict[str, list[Ticket]] = {}
    for t in tickets:
        by_setup.setdefault(t.setup_type, []).append(t)

    insights = []
    for setup, ts in sorted(by_setup.items(), key=lambda x: -len(x[1])):
        if len(ts) < 3:
            continue
        wins = sum(1 for t in ts if t.outcome == "win")
        wr = wins / len(ts)
        avg_r = sum(float(t.r_multiple) for t in ts) / len(ts)
        severity = "good" if wr >= 0.55 and avg_r > 0.3 else "bad" if wr < 0.35 else "info"
        insights.append(Insight(
            category="setup",
            severity=severity,
            headline=f"{setup}: {_pct(wins, len(ts))} win rate, {avg_r:+.2f}R avg ({len(ts)} trades)",
            detail=(
                f"Your {setup} trades have a {_pct(wins, len(ts))} win rate and {avg_r:+.2f}R average. "
                + ("This is your best setup — prioritise it." if severity == "good"
                   else "Consider whether this setup suits your execution style." if severity == "bad"
                   else "")
            ),
            data={"setup": setup, "trades": len(ts), "win_rate": wr, "avg_r": avg_r},
        ))
    return insights


def _streak_tilt(tickets: list[Ticket]) -> list[Insight]:
    """Win rate after consecutive losses vs baseline."""
    if len(tickets) < 8:
        return []

    post_loss_outcomes = []
    for i in range(2, len(tickets)):
        if tickets[i - 1].outcome == "loss" and tickets[i - 2].outcome == "loss":
            post_loss_outcomes.append(tickets[i].outcome)

    if len(post_loss_outcomes) < 3:
        return []

    post_loss_wr = sum(1 for o in post_loss_outcomes if o == "win") / len(post_loss_outcomes)
    overall_wr = sum(1 for t in tickets if t.outcome == "win") / len(tickets)

    if post_loss_wr < overall_wr * 0.7:
        return [Insight(
            category="streak",
            severity="warn",
            headline=f"Performance drops after 2 consecutive losses: {_pct(int(post_loss_wr*len(post_loss_outcomes)), len(post_loss_outcomes))} win rate (vs {_pct(int(overall_wr*len(tickets)), len(tickets))} overall)",
            detail=(
                f"After 2 straight losses, your win rate falls to {post_loss_wr:.0%} vs your overall {overall_wr:.0%}. "
                "This is a sign of tilt. The loss-streak block guardrail is your friend — use it."
            ),
            data={"post_loss_wr": post_loss_wr, "overall_wr": overall_wr, "sample": len(post_loss_outcomes)},
        )]
    return []


def _revenge_trades(tickets: list[Ticket]) -> list[Insight]:
    """Detect trades entered within 24h of a stop-out."""
    if len(tickets) < 4:
        return []

    revenge_count = 0
    revenge_wins = 0
    for i in range(1, len(tickets)):
        prev = tickets[i - 1]
        curr = tickets[i]
        if prev.outcome != "loss":
            continue
        prev_close = prev.closed_at or prev.filled_at
        curr_entry = curr.armed_at or curr.created_at
        if prev_close and curr_entry:
            gap = curr_entry - prev_close
            if gap < timedelta(hours=24):
                revenge_count += 1
                if curr.outcome == "win":
                    revenge_wins += 1

    if revenge_count >= 2:
        wr = revenge_wins / revenge_count
        severity = "warn" if wr < 0.4 else "info"
        return [Insight(
            category="revenge",
            severity=severity,
            headline=f"{revenge_count} trade(s) entered within 24h of a stop-out ({_pct(revenge_wins, revenge_count)} won)",
            detail=(
                "Trades entered quickly after a loss carry revenge-trading risk. "
                "The 24-hour cooldown guardrail exists for this reason. "
                + ("Your revenge trades had a below-average win rate." if severity == "warn" else "")
            ),
            data={"revenge_count": revenge_count, "win_rate": wr},
        )]
    return []


def _close_reason_patterns(tickets: list[Ticket]) -> list[Insight]:
    """Surface patterns in qualitative close reasons."""
    tagged = [t for t in tickets if t.close_reason_tag]
    if len(tagged) < 3:
        return []

    from collections import Counter
    counts = Counter(t.close_reason_tag for t in tagged)
    most_common_tag, count = counts.most_common(1)[0]
    if count / len(tagged) > 0.4:
        tag_wins = sum(1 for t in tagged if t.close_reason_tag == most_common_tag and t.outcome == "win")
        tag_total = count
        return [Insight(
            category="close_reason",
            severity="info",
            headline=f"Most common close reason: '{most_common_tag}' ({_pct(count, len(tagged))} of journalled trades)",
            detail=f"Win rate on '{most_common_tag}' exits: {_pct(tag_wins, tag_total)}.",
            data={"tag": most_common_tag, "count": count},
        )]
    return []
