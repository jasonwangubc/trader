"""Single shared broker instance for the process.

Import get_broker() wherever a broker is needed.
Paper mode wraps the live broker for quotes but intercepts orders.
"""
from __future__ import annotations

from app.brokers.base import BrokerInterface

_broker: BrokerInterface | None = None


def get_broker() -> BrokerInterface:
    global _broker
    if _broker is None:
        _broker = _make_broker()
    return _broker


def _make_broker() -> BrokerInterface:
    from app.config import get_settings
    settings = get_settings()

    # Always instantiate Questrade for live data/auth.
    from app.brokers.questrade import QuestradeBroker
    qt = QuestradeBroker()

    if settings.paper_mode_default:
        from app.brokers.paper import PaperBroker
        return PaperBroker(quote_source=qt)

    return qt
