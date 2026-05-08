"""Per-user broker instances.

Each user has their own Questrade credentials stored in the settings table.
get_broker(user_id, session) returns a broker for that user, creating one
if it doesn't already exist in the process cache.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerInterface

# Cache: user_id → broker instance
_brokers: dict[str, BrokerInterface] = {}


def get_broker(user_id: str = "user_default", session: AsyncSession | None = None) -> BrokerInterface:
    """Return (or create) the broker for the given user."""
    if user_id not in _brokers:
        _brokers[user_id] = _make_broker(user_id)
    return _brokers[user_id]


def invalidate_broker(user_id: str) -> None:
    """Force recreating the broker on next call (e.g. after token update)."""
    _brokers.pop(user_id, None)


def _make_broker(user_id: str) -> BrokerInterface:
    from app.config import get_settings
    settings = get_settings()

    from app.brokers.questrade import QuestradeBroker
    qt = QuestradeBroker(user_id=user_id)

    if settings.paper_mode_default:
        from app.brokers.paper import PaperBroker
        return PaperBroker(quote_source=qt)

    return qt
