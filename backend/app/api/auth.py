"""User identity extraction for FastAPI endpoints.

The Next.js frontend reads the Clerk session and injects the verified user ID
as the X-User-Id header before forwarding requests to this API. This is trusted
because all browser requests go through the Next.js server (which runs Clerk
middleware) — browsers never call FastAPI directly.

In development (APP_ENV=development) or when no header is present, the default
placeholder user "user_default" is used so the app works without Clerk during
local single-user development.
"""
from __future__ import annotations

from fastapi import Header, Request
from app.config import get_settings

USER_DEFAULT = "user_default"


async def get_user_id(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> str:
    """FastAPI dependency — returns the current user's ID.

    In production the Next.js proxy always sets X-User-Id from the verified
    Clerk session. In development it falls back to USER_DEFAULT so single-user
    local dev works without Clerk configured.
    """
    if x_user_id:
        return x_user_id
    # Fallback for local dev (no Clerk configured)
    return USER_DEFAULT
