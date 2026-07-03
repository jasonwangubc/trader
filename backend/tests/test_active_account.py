"""Active-account scoping: equity basis, setting validation, self-healing."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.services.accounts_service import (
    get_active_account_id,
    get_household_equity,
    set_active_account_id,
)
from app.services.settings_service import set_setting
from tests.factories import make_account

USER = "test_user"


async def test_household_equity_sums_all_accounts_when_unset(db_session):
    await make_account(db_session, user_id=USER, account_type="RRSP", equity=Decimal("50000"))
    await make_account(db_session, user_id=USER, account_type="RESP", equity=Decimal("20000"))
    equity = await get_household_equity(db_session, USER)
    assert equity == {"CAD": Decimal("70000.000000")}


async def test_active_account_scopes_equity(db_session):
    rrsp = await make_account(db_session, user_id=USER, account_type="RRSP", equity=Decimal("50000"))
    await make_account(db_session, user_id=USER, account_type="RESP", equity=Decimal("20000"))

    await set_active_account_id(db_session, USER, rrsp.id)
    equity = await get_household_equity(db_session, USER)
    assert equity == {"CAD": Decimal("50000.000000")}

    # Explicit opt-out returns the full household.
    full = await get_household_equity(db_session, USER, scope_to_active=False)
    assert full == {"CAD": Decimal("70000.000000")}

    # Clearing restores household behavior.
    await set_active_account_id(db_session, USER, None)
    assert await get_active_account_id(db_session, USER) is None
    equity = await get_household_equity(db_session, USER)
    assert equity == {"CAD": Decimal("70000.000000")}


async def test_explicit_account_id_wins_over_active_setting(db_session):
    rrsp = await make_account(db_session, user_id=USER, account_type="RRSP", equity=Decimal("50000"))
    resp = await make_account(db_session, user_id=USER, account_type="RESP", equity=Decimal("20000"))
    await set_active_account_id(db_session, USER, rrsp.id)

    # Sizing against a specific account (the ticket's) ignores the active setting.
    equity = await get_household_equity(db_session, USER, account_id=resp.id)
    assert equity == {"CAD": Decimal("20000.000000")}


async def test_cannot_set_other_users_account(db_session):
    foreign = await make_account(db_session, user_id="someone_else", equity=Decimal("10000"))
    with pytest.raises(ValueError):
        await set_active_account_id(db_session, USER, foreign.id)


async def test_stale_setting_treated_as_unset(db_session):
    """A dangling active_account_id (deleted/deactivated account) self-heals to None."""
    await make_account(db_session, user_id=USER, account_type="RRSP", equity=Decimal("50000"))
    await set_setting(db_session, f"{USER}:active_account_id", str(uuid.uuid4()))
    assert await get_active_account_id(db_session, USER) is None
    equity = await get_household_equity(db_session, USER)
    assert equity == {"CAD": Decimal("50000.000000")}


async def test_deactivated_active_account_treated_as_unset(db_session):
    rrsp = await make_account(db_session, user_id=USER, account_type="RRSP", equity=Decimal("50000"))
    await make_account(db_session, user_id=USER, account_type="RESP", equity=Decimal("20000"))
    await set_active_account_id(db_session, USER, rrsp.id)

    rrsp.is_active = False
    await db_session.flush()
    assert await get_active_account_id(db_session, USER) is None
    equity = await get_household_equity(db_session, USER)
    # RRSP is inactive so only RESP counts in the household fallback.
    assert equity == {"CAD": Decimal("20000.000000")}
