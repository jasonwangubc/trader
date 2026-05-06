"""Trailing stop suggestions — computed from the ticket's current price relative
to entry and risk.

Minervini's trailing rules:
  At +1R:  Move stop to breakeven (entry price). Lock in zero-loss.
  At +2R:  Trail stop to entry + 0.5R. Lock in a small gain.
  At +3R:  Move stop to entry + 1R. Starting to let it run.
  At +5R:  Sell 1/3, trail remainder to entry + 2R.
  At +10R: Sell another 1/3, use 10-week MA as trailing stop.

We compute these against the last available daily close, not live intraday price.
Suggestions are informational — the user acts on them manually (or via exit ladder).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


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

    # Already below stop — don't suggest (stop should have fired)
    if open_r < -1.1:
        return None

    # Not yet at breakeven — no trailing action
    if open_r < 1.0:
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=None,
            action=f"Trade at {open_r:+.2f}R — no trailing action yet. Hold stop at {stop_price:.2f}.",
            urgency="info",
            milestone_label="Watching",
        )

    # Determine milestone
    if open_r >= 10:
        new_stop = entry_price + per_share_risk * 2
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop.quantize(Decimal("0.01")),
            action=f"At +{open_r:.1f}R: sell 1/3, trail remainder stop to +2R ({float(new_stop):.2f}).",
            urgency="act",
            milestone_label="+10R milestone",
        )
    if open_r >= 5:
        new_stop = entry_price + per_share_risk * 2
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop.quantize(Decimal("0.01")),
            action=f"At +{open_r:.1f}R: consider selling 1/3. Trail stop to +2R ({float(new_stop):.2f}).",
            urgency="act",
            milestone_label="+5R milestone",
        )
    if open_r >= 3:
        new_stop = entry_price + per_share_risk
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop.quantize(Decimal("0.01")),
            action=f"At +{open_r:.1f}R: trail stop to +1R ({float(new_stop):.2f}).",
            urgency="warn",
            milestone_label="+3R milestone",
        )
    if open_r >= 2:
        new_stop = entry_price + per_share_risk * Decimal("0.5")
        return TrailingSuggestion(
            open_r=round(open_r, 2),
            new_stop=new_stop.quantize(Decimal("0.01")),
            action=f"At +{open_r:.1f}R: trail stop to +0.5R ({float(new_stop):.2f}). Lock in a gain.",
            urgency="warn",
            milestone_label="+2R milestone",
        )
    # +1R — move to breakeven
    return TrailingSuggestion(
        open_r=round(open_r, 2),
        new_stop=entry_price.quantize(Decimal("0.01")),
        action=f"At +{open_r:.1f}R: move stop to breakeven ({float(entry_price):.2f}). Remove risk of loss.",
        urgency="warn",
        milestone_label="+1R milestone",
    )
