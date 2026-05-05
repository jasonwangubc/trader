"""CRUD helpers for the settings table (key-value store)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting


async def get_setting(session: AsyncSession, key: str) -> str | None:
    row = await session.get(Setting, key)
    if row is None:
        return None
    v = row.value
    return v if isinstance(v, str) else str(v)


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_at=datetime.now(timezone.utc)))
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)


async def del_setting(session: AsyncSession, key: str) -> None:
    row = await session.get(Setting, key)
    if row is not None:
        await session.delete(row)
