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
    # Sessions run with autoflush=False — flush so same-session reads see this.
    await session.flush()


async def get_setting_json(session: AsyncSession, key: str) -> dict | None:
    """Return a structured (dict) setting value, or None if missing/not a dict.
    The value column is JSONB, so dicts round-trip natively."""
    row = await session.get(Setting, key)
    if row is None or not isinstance(row.value, dict):
        return None
    return row.value


async def set_setting_json(session: AsyncSession, key: str, value: dict) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value, updated_at=datetime.now(timezone.utc)))
    else:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def del_setting(session: AsyncSession, key: str) -> None:
    row = await session.get(Setting, key)
    if row is not None:
        await session.delete(row)
        await session.flush()
