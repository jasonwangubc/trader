"""Wheel-strategy API.

Endpoints:
  GET  /api/wheel/candidates           — latest scan results, filterable
  POST /api/wheel/scan                 — kick off a wheel scan (sync; returns when done)
  GET  /api/wheel/scan/status          — last-scan summary
  GET  /api/wheel/chain/{symbol}       — full options chain near 30 DTE for one symbol
  POST /api/wheel/correlation          — correlation + sector breakdown for a basket
  GET  /api/wheel/concentration        — concentration report for the user's current open wheel + held positions
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_id
from app.db.models import (
    Account,
    OptionStatus,
    OptionTicket,
    Position,
    WheelCandidate,
)
from app.db.session import SessionLocal, get_session
from app.services.correlation_service import correlation_report
from app.services.options_chain_service import (
    days_to_expiry,
    fetch_chain,
    list_expiries,
    pick_expiry_near_dte,
)
from app.services.wheel_service import WheelScanConfig, scan_wheel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wheel", tags=["wheel"])


# ---------- Scan state (in-process, mirrors screener pattern) ----------

_SCAN_STATE: dict[str, dict] = {}     # per-user last scan summary
_SCAN_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(user_id: str) -> asyncio.Lock:
    lock = _SCAN_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _SCAN_LOCKS[user_id] = lock
    return lock


# ---------- Shapes ----------

class WheelCandidateOut(BaseModel):
    id: uuid.UUID
    symbol: str
    sector: str | None
    strategy: str
    last_price: Decimal
    expiry: datetime
    dte: int
    strike: Decimal
    option_type: str
    bid: Decimal | None
    ask: Decimal | None
    mid: Decimal
    last: Decimal | None
    bid_ask_spread_pct: Decimal | None
    open_interest: int
    volume: int
    implied_volatility: Decimal | None
    delta_approx: Decimal | None
    premium_yield_pct: Decimal
    annualized_yield_pct: Decimal
    otm_pct: Decimal
    capital_at_risk: Decimal
    breakeven: Decimal
    earnings_before_expiry: bool
    next_earnings_date: datetime | None
    score: Decimal
    score_breakdown: dict
    scanned_at: datetime


def _to_out(c: WheelCandidate) -> WheelCandidateOut:
    return WheelCandidateOut.model_validate(c, from_attributes=True)


class ScanRequest(BaseModel):
    target_dte:           int   = Field(default=30, ge=7, le=60)
    dte_tolerance:        int   = Field(default=10, ge=0, le=20)
    min_annualized_yield: float = Field(default=0.10, ge=0.0, le=1.0)
    max_annualized_yield: float = Field(default=0.50, ge=0.05, le=2.0)
    target_csp_otm_pct:   float = Field(default=0.07, ge=0.01, le=0.20)
    target_cc_otm_pct:    float = Field(default=0.05, ge=0.01, le=0.20)
    min_open_interest:    int   = Field(default=50, ge=0)
    min_underlying_price: float = Field(default=10.0, ge=1.0)
    max_candidates_to_scan: int = Field(default=60, ge=5, le=200)
    min_composite_score:  float = Field(default=0.30, ge=0.0, le=1.0)
    max_implied_volatility: float = Field(default=0.55, ge=0.10, le=2.0)


class CorrelationRequest(BaseModel):
    symbols: list[str] = Field(min_length=2, max_length=50)
    notionals: dict[str, float] | None = None
    lookback_days: int = 90


# ---------- List candidates ----------

@router.get("/candidates", response_model=list[WheelCandidateOut])
async def list_candidates(
    strategy: str | None = None,        # "csp" | "cc"
    min_score: float = 0.0,
    min_annualized_yield: float = 0.0,
    max_annualized_yield: float = 10.0,
    skip_earnings: bool = False,
    sector: str | None = None,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> list[WheelCandidateOut]:
    q = (
        select(WheelCandidate)
        .where(WheelCandidate.user_id == user_id)
        .order_by(WheelCandidate.score.desc())
        .limit(limit)
    )
    rows = (await session.execute(q)).scalars().all()
    out: list[WheelCandidate] = []
    for r in rows:
        if strategy and r.strategy != strategy:
            continue
        if float(r.score) < min_score:
            continue
        if float(r.annualized_yield_pct) < min_annualized_yield:
            continue
        if float(r.annualized_yield_pct) > max_annualized_yield:
            continue
        if skip_earnings and r.earnings_before_expiry:
            continue
        if sector and (r.sector or "").lower() != sector.lower():
            continue
        out.append(r)
    return [_to_out(c) for c in out]


# ---------- Scan ----------

@router.post("/scan")
async def trigger_scan(
    body: ScanRequest | None = None,
    user_id: str = Depends(get_user_id),
) -> dict:
    cfg = WheelScanConfig(**(body.model_dump() if body else {}))
    lock = _lock_for(user_id)
    if lock.locked():
        raise HTTPException(409, "Scan already running for this user")
    async with lock:
        _SCAN_STATE[user_id] = {"running": True, "started_at": datetime.now(timezone.utc).isoformat()}
        # Use a fresh session — this is a long-running task, don't tie up the request session
        async with SessionLocal() as session:
            try:
                summary = await scan_wheel(session, user_id, cfg)
            except Exception as exc:
                log.exception("wheel scan failed for %s", user_id)
                _SCAN_STATE[user_id] = {"running": False, "error": str(exc)}
                raise HTTPException(500, f"Scan failed: {exc}")
        _SCAN_STATE[user_id] = {"running": False, **summary}
    return _SCAN_STATE[user_id]


@router.get("/scan/status")
async def scan_status(user_id: str = Depends(get_user_id)) -> dict:
    return _SCAN_STATE.get(user_id, {"running": False, "scanned": 0, "candidates": 0})


# ---------- Chain ----------

class OptionChainRow(BaseModel):
    strike: Decimal
    option_type: str
    bid: Decimal | None
    ask: Decimal | None
    mid: Decimal
    last: Decimal | None
    open_interest: int
    volume: int
    implied_volatility: Decimal | None


class OptionChainOut(BaseModel):
    symbol: str
    expiry: date
    dte: int
    puts: list[OptionChainRow]
    calls: list[OptionChainRow]


@router.get("/chain/{symbol}", response_model=OptionChainOut)
async def get_chain(
    symbol: str,
    dte: int = 30,
    tolerance: int = 14,
    user_id: str = Depends(get_user_id),
) -> OptionChainOut:
    symbol = symbol.upper().strip()
    expiries = await list_expiries(symbol)
    target = pick_expiry_near_dte(expiries, dte, tolerance)
    if target is None:
        raise HTTPException(404, f"No expiry near {dte} DTE for {symbol}")
    chain = await fetch_chain(symbol, target)
    if chain is None:
        raise HTTPException(502, f"Failed to fetch chain for {symbol} @ {target.isoformat()}")
    def _row(q) -> OptionChainRow:
        return OptionChainRow(
            strike=q.strike, option_type=q.option_type,
            bid=q.bid, ask=q.ask, mid=q.mid, last=q.last,
            open_interest=q.open_interest, volume=q.volume,
            implied_volatility=q.implied_volatility,
        )
    return OptionChainOut(
        symbol=symbol,
        expiry=target,
        dte=days_to_expiry(target),
        puts=[_row(q) for q in chain.puts],
        calls=[_row(q) for q in chain.calls],
    )


# ---------- Correlation (basket) ----------

@router.post("/correlation")
async def post_correlation(
    body: CorrelationRequest,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> dict:
    report = await correlation_report(
        session,
        body.symbols,
        notional_by_symbol=body.notionals,
        lookback_days=body.lookback_days,
    )
    return {
        "symbols": report.symbols,
        "total_notional": report.total_notional,
        "pairs": [asdict(p) for p in report.pairs],
        "sectors": [asdict(s) for s in report.sectors],
        "flagged_pairs": [asdict(p) for p in report.flagged_pairs],
        "flagged_sectors": [asdict(s) for s in report.flagged_sectors],
        "single_name_warnings": report.single_name_warnings,
    }


# ---------- Concentration of current portfolio + open wheel ----------

@router.get("/concentration")
async def get_concentration(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_user_id),
) -> dict:
    """Concentration report for the user's CURRENT wheel exposure:
    open OptionTickets (CSPs + CCs) plus existing long positions in 100-lot multiples.

    Each position contributes its at-risk notional:
      - CSP: strike * 100 * contracts
      - CC : underlying_price * 100 * contracts (use position avg_cost as a proxy)
      - Long stock: avg_cost * quantity
    """
    # Open options
    opts = (
        await session.execute(
            select(OptionTicket).where(
                OptionTicket.user_id == user_id,
                OptionTicket.status == OptionStatus.OPEN.value,
            )
        )
    ).scalars().all()

    # Holdings in 100-lot multiples (potential wheel underliers)
    holdings = (
        await session.execute(
            select(Position, Account)
            .join(Account, Position.account_id == Account.id)
            .where(Account.user_id == user_id, Position.quantity >= 100)
        )
    ).all()

    notional_by_symbol: dict[str, float] = {}
    breakdown: list[dict] = []

    for o in opts:
        sym = o.underlying_symbol
        if o.strategy == "cash_secured_put":
            n = float(o.strike_price) * 100 * o.contracts
        elif o.strategy == "covered_call":
            # Approximate at strike (close enough for concentration)
            n = float(o.strike_price) * 100 * o.contracts
        else:
            n = 0.0
        notional_by_symbol[sym] = notional_by_symbol.get(sym, 0.0) + n
        breakdown.append({
            "kind": "option",
            "symbol": sym,
            "strategy": o.strategy,
            "contracts": o.contracts,
            "notional": round(n, 2),
        })

    for pos, _acct in holdings:
        n = float(pos.avg_cost) * float(pos.quantity)
        notional_by_symbol[pos.symbol] = notional_by_symbol.get(pos.symbol, 0.0) + n
        breakdown.append({
            "kind": "stock",
            "symbol": pos.symbol,
            "quantity": int(pos.quantity),
            "notional": round(n, 2),
        })

    if not notional_by_symbol:
        return {"empty": True, "breakdown": [], "report": None}

    report = await correlation_report(
        session,
        list(notional_by_symbol.keys()),
        notional_by_symbol=notional_by_symbol,
    )
    return {
        "empty": False,
        "breakdown": breakdown,
        "report": {
            "symbols": report.symbols,
            "total_notional": report.total_notional,
            "pairs": [asdict(p) for p in report.pairs],
            "sectors": [asdict(s) for s in report.sectors],
            "flagged_pairs": [asdict(p) for p in report.flagged_pairs],
            "flagged_sectors": [asdict(s) for s in report.flagged_sectors],
            "single_name_warnings": report.single_name_warnings,
        },
    }
