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
    has_token: bool = False    # True if a token is stored, even if validation failed


def _token_key(user_id: str) -> str:
    return f"{user_id}:questrade_refresh_token" if user_id != "user_default" else "questrade_refresh_token"


async def _has_token(session: AsyncSession, user_id: str) -> bool:
    db_token = await get_setting(session, _token_key(user_id))
    if db_token:
        return True
    if user_id == "user_default":
        from app.config import get_settings
        return bool(get_settings().questrade_refresh_token)
    return False


async def _probe_questrade(user_id: str) -> tuple[bool, str]:
    """Try to obtain a working access token. Returns (ok, message)."""
    try:
        broker = get_broker(user_id=user_id)
        # ensure_token uses the cached state when valid; only first call hits the
        # auth endpoint, so this is cheap to call from a status check.
        ensure = getattr(broker, "ensure_token", None)
        if ensure is None:
            # Paper or other broker without auth — treat as "not applicable, ok"
            return True, "Connected"
        await ensure()
        return True, "Connected"
    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:  # network, DNS, etc.
        return False, f"Could not reach Questrade: {exc}"


@router.get("/questrade", response_model=ConnectionStatus)
async def questrade_status(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
    validate: bool = True,
) -> ConnectionStatus:
    """Check if Questrade is connected for this user.

    By default actually validates the token by exercising the broker's
    ensure_token() (uses cached state when fresh, so it's cheap on warm calls).
    Pass `validate=false` to do a presence-only check.
    """
    has_token = await _has_token(session, user_id)
    if not has_token:
        return ConnectionStatus(
            connected=False,
            has_token=False,
            message="Not connected — paste your Questrade token below.",
        )

    if not validate:
        return ConnectionStatus(connected=True, has_token=True, message="Token saved (not validated).")

    ok, msg = await _probe_questrade(user_id)
    return ConnectionStatus(connected=ok, has_token=True, message=msg)


@router.post("/questrade/token", response_model=ConnectionStatus)
async def save_questrade_token(
    body: QuestradTokenIn,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> ConnectionStatus:
    """Save (or replace) the Questrade refresh token for this user, then test it.

    On validation failure we leave the token saved so the user can re-test
    after fixing whatever's wrong upstream — but the response makes the
    failure obvious.
    """
    await set_setting(session, _token_key(user_id), body.refresh_token.strip())
    await session.commit()
    # Force broker to re-authenticate with the new token on next request
    invalidate_broker(user_id)

    ok, msg = await _probe_questrade(user_id)
    if not ok:
        # Surface as a 502 so the frontend's catch-block treats it as an error,
        # but include the diagnostic in the detail.
        raise HTTPException(status_code=502, detail=msg)
    return ConnectionStatus(connected=True, has_token=True, message=msg)


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
