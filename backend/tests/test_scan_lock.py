"""Concurrent-scan guard: only one universe scan may run per process.

Regression for the restart race: nightly loop + startup stale check both
fired _do_sync on an evening restart, and the second run_screener died on
the screener_scores unique(symbol) constraint after a full 27-minute scan.
"""
from __future__ import annotations

import pytest

from app.services import screener_service
from app.services.screener_service import ScanInProgressError, run_screener, scan_in_progress


async def test_second_scan_is_rejected_while_lock_held(db_session):
    assert not scan_in_progress()
    async with screener_service._scan_lock:
        assert scan_in_progress()
        with pytest.raises(ScanInProgressError):
            await run_screener(db_session, mode="manual")
    assert not scan_in_progress()
