"""User settings API — Questrade token management and preferences."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_id
from app.brokers.registry import get_broker, invalidate_broker
from app.db.session import get_session
from app.services.settings_service import del_setting, get_setting, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class QuestradTokenIn(BaseModel):
    refresh_token: str


class ConnectionStatus(BaseModel):
    connected: bool
    message: str


@router.get("/questrade", response_model=ConnectionStatus)
async def questrade_status(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> ConnectionStatus:
    """Check if Questrade is connected for this user."""
    token_key = f"{user_id}:questrade_refresh_token" if user_id != "user_default" else "questrade_refresh_token"
    token = await get_setting(session, token_key)
    has_env_token = bool(__import__("app.config", fromlist=["get_settings"]).get_settings().questrade_refresh_token)
    connected = bool(token or has_env_token)
    return ConnectionStatus(
        connected=connected,
        message="Connected" if connected else "Not connected — add your Questrade token below",
    )


@router.post("/questrade/token", response_model=ConnectionStatus)
async def save_questrade_token(
    body: QuestradTokenIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> ConnectionStatus:
    """Save (or replace) the Questrade refresh token for this user."""
    token_key = f"{user_id}:questrade_refresh_token" if user_id != "user_default" else "questrade_refresh_token"
    await set_setting(session, token_key, body.refresh_token.strip())
    await session.commit()
    # Force broker to re-authenticate with the new token on next request
    invalidate_broker(user_id)
    return ConnectionStatus(connected=True, message="Token saved. Testing connection…")


@router.delete("/questrade/token")
async def disconnect_questrade(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> dict:
    """Remove the Questrade token for this user."""
    token_key = f"{user_id}:questrade_refresh_token" if user_id != "user_default" else "questrade_refresh_token"
    await del_setting(session, token_key)
    await session.commit()
    invalidate_broker(user_id)
    return {"message": "Disconnected"}
