"""Append-only audit log helper. Every state-change in the system flows here."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def log_event(
    session: AsyncSession,
    *,
    actor: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> None:
    """Append an audit event. Caller is responsible for the surrounding transaction."""
    session.add(
        AuditLog(
            actor=actor,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        )
    )
