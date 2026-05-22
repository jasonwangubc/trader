"""Wheel-strategy candidate scanner.

The wheel:
  1. Sell a cash-secured put (CSP) on a stock you'd be happy to own.
  2. If assigned, sell a covered call (CC) at or above your cost basis.
  3. If called away, return to step 1.

This module finds candidate CSPs and CCs that meet:
  - Target DTE (default 30, tolerance ±10 days)
  - Target annualized yield (default 10-20%)
  - Quality filter on the underlying (large-cap proxy, profitable, scored well)
  - Liquidity floor (open interest + tight enough bid-ask)
  - Earnings-before-expiry flagged (we *don't* exclude outright — user decides)

The scanner persists results in `wheel_candidates`. The whole table is
rewritten per user on each scan (delete-then-insert).
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    EarningsDate,
    Position,
    ScreenerScore,
    WheelCandidate,
)
from app.services.options_chain_service import (
    OptionQuote,
    days_to_expiry,
    fetch_chain,
    list_expiries,
    pick_expiry_near_dte,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WheelScanConfig:
    target_dte: int = 30
    dte_tolerance: int = 10
    min_annualized_yield: float = 0.10
    max_annualized_yield: float = 0.50          # >50% annualized = trap, not income
    target_csp_otm_pct: float = 0.07            # 7% OTM ~ delta 0.25-ish for a 30 DTE blue chip
    csp_otm_band: float = 0.05                  # accept 5%-10% OTM (target ± band/2)
    target_cc_otm_pct: float = 0.05             # 5% OTM call on owned shares
    cc_otm_band: float = 0.05
    min_open_interest: int = 50
    max_bid_ask_spread_pct: float = 0.25        # ask must be within 25% of mid
    min_underlying_price: float = 10.0          # avoid penny names where 1c = 1% slippage
    max_candidates_to_scan: int = 60            # cap on symbols (yfinance is slow)
    min_composite_score: float = 0.30           # quality floor from screener_scores
    # Stocks-you-can-hold-without-worry filter: cap IV. >55% IV means the market
    # is pricing in major event risk (earnings, M&A, biotech catalyst). Wheel
    # traders should avoid those — the high premium is the market warning you.
    max_implied_volatility: float = 0.55


# ---------- Quality filter ----------

# Sectors that wheel traders generally over-concentrate in. We don't ban them,
# we just use them for the concentration report later.
_DEFENSIVE_SECTORS = {
    "Consumer Defensive",
    "Healthcare",
    "Utilities",
    "Communication Services",
}


async def _quality_candidates(
    session: AsyncSession,
    cfg: WheelScanConfig,
) -> list[ScreenerScore]:
    """Pull large-cap-ish, scored, profitable symbols from screener_scores.

    Heuristics (since we don't store market cap directly):
      - has fundamental data (fundamental_score > 0.3)
      - has a TT score >= 4 OR positive EPS rank (avoid garbage)
      - composite_score is decent (>= cfg.min_composite_score)
      - last_close above the penny-stock floor
      - sector is known (filters out a lot of micro-cap fluff)
      - has earnings_annual_growth >= -5% (skip outright losers)
    """
    # Sort by fundamental_score, not composite_score. composite_score is biased
    # toward momentum/breakout setups (it zeroes stocks not "at pivot"), but for
    # wheel candidates we want quality first — profitability, margin, ROE — and
    # we'd actually rather *avoid* stocks that just broke out (premiums are
    # already inflated).
    q = (
        select(ScreenerScore)
        .order_by(
            ScreenerScore.fundamental_score.desc(),
            ScreenerScore.smr_rank.desc().nulls_last(),
        )
    )
    rows = (await session.execute(q)).scalars().all()

    kept: list[ScreenerScore] = []
    for r in rows:
        last = float(r.last_close or 0)
        if last < cfg.min_underlying_price:
            continue
        if r.sector is None:
            continue
        if float(r.fundamental_score or 0) < 0.3:
            continue
        # For wheel we use min_composite_score as a soft floor — wheel names
        # don't need an active breakout setup, they need quality + stability.
        if float(r.composite_score or 0) < cfg.min_composite_score:
            continue
        # Skip outright losers but don't require a hot growth name —
        # wheel works fine on mature, slow-growing blue chips.
        growth = float(r.earnings_annual_growth) if r.earnings_annual_growth is not None else 0.0
        if growth < -0.05:
            continue
        # Skip TSX symbols — Yahoo's Canadian options coverage is unreliable.
        if r.symbol.endswith(".TO") or r.symbol.endswith(".V"):
            continue
        kept.append(r)
        if len(kept) >= cfg.max_candidates_to_scan:
            break
    return kept


# ---------- Quote helpers ----------

def _spread_pct(q: OptionQuote) -> Decimal | None:
    if q.bid is None or q.ask is None or q.bid <= 0 or q.ask <= 0 or q.mid <= 0:
        return None
    return (q.ask - q.bid) / q.mid


def _approx_delta_csp(strike: Decimal, spot: Decimal, dte: int, iv: Decimal | None) -> Decimal | None:
    """Crude delta approximation for an OTM put, using BSM with r=0, q=0.

    Good enough for ranking — not for hedging. Returns absolute value (0-1).
    """
    if iv is None or iv <= 0 or spot <= 0 or strike <= 0 or dte <= 0:
        return None
    try:
        s = float(spot)
        k = float(strike)
        sigma = float(iv)
        t = dte / 365.0
        d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
        # N(-d1) for a put delta in absolute terms — and we want the *short put*
        # delta the user sees on their broker, so |delta| = N(-d1).
        n = 0.5 * (1 - math.erf(d1 / math.sqrt(2)))
        return Decimal(str(round(max(0.0, min(1.0, 1 - n)), 4)))
        # Note: put delta is -N(-d1); we present the *risk* magnitude which is
        # 1 - N(d1) = N(-d1). Above expresses |delta_put| = N(-d1).
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def _approx_delta_cc(strike: Decimal, spot: Decimal, dte: int, iv: Decimal | None) -> Decimal | None:
    """|delta| for an OTM call. Returns 0-1."""
    if iv is None or iv <= 0 or spot <= 0 or strike <= 0 or dte <= 0:
        return None
    try:
        s = float(spot)
        k = float(strike)
        sigma = float(iv)
        t = dte / 365.0
        d1 = (math.log(s / k) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
        n = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        return Decimal(str(round(max(0.0, min(1.0, n)), 4)))
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


# ---------- Picking the contract ----------

def _pick_best_csp(puts: list[OptionQuote], spot: Decimal, cfg: WheelScanConfig) -> OptionQuote | None:
    """Among OTM puts, choose the one closest to cfg.target_csp_otm_pct that
    meets the liquidity, spread, and IV floors."""
    low = cfg.target_csp_otm_pct - cfg.csp_otm_band / 2
    high = cfg.target_csp_otm_pct + cfg.csp_otm_band / 2
    candidates: list[OptionQuote] = []
    for q in puts:
        if q.strike >= spot:
            continue  # not OTM
        otm = float((spot - q.strike) / spot)
        if not (low <= otm <= high):
            continue
        if q.open_interest < cfg.min_open_interest:
            continue
        sp = _spread_pct(q)
        if sp is not None and float(sp) > cfg.max_bid_ask_spread_pct:
            continue
        if q.implied_volatility is not None and float(q.implied_volatility) > cfg.max_implied_volatility:
            continue
        candidates.append(q)
    if not candidates:
        # Fallback: relax the OTM band but keep liquidity + IV ceiling
        for q in puts:
            if q.strike >= spot:
                continue
            otm = float((spot - q.strike) / spot)
            if not (0.03 <= otm <= 0.15):
                continue
            if q.open_interest < cfg.min_open_interest:
                continue
            if q.implied_volatility is not None and float(q.implied_volatility) > cfg.max_implied_volatility:
                continue
            candidates.append(q)
    if not candidates:
        return None

    # Best = closest to target OTM with the highest yield
    def _key(q: OptionQuote) -> tuple[float, float]:
        otm = float((spot - q.strike) / spot)
        return (abs(otm - cfg.target_csp_otm_pct), -float(q.mid))
    candidates.sort(key=_key)
    return candidates[0]


def _pick_best_cc(calls: list[OptionQuote], spot: Decimal, cfg: WheelScanConfig) -> OptionQuote | None:
    low = cfg.target_cc_otm_pct - cfg.cc_otm_band / 2
    high = cfg.target_cc_otm_pct + cfg.cc_otm_band / 2
    candidates: list[OptionQuote] = []
    for q in calls:
        if q.strike <= spot:
            continue  # not OTM
        otm = float((q.strike - spot) / spot)
        if not (low <= otm <= high):
            continue
        if q.open_interest < cfg.min_open_interest:
            continue
        sp = _spread_pct(q)
        if sp is not None and float(sp) > cfg.max_bid_ask_spread_pct:
            continue
        if q.implied_volatility is not None and float(q.implied_volatility) > cfg.max_implied_volatility:
            continue
        candidates.append(q)
    if not candidates:
        for q in calls:
            if q.strike <= spot:
                continue
            otm = float((q.strike - spot) / spot)
            if not (0.02 <= otm <= 0.12):
                continue
            if q.open_interest < cfg.min_open_interest:
                continue
            if q.implied_volatility is not None and float(q.implied_volatility) > cfg.max_implied_volatility:
                continue
            candidates.append(q)
    if not candidates:
        return None
    def _key(q: OptionQuote) -> tuple[float, float]:
        otm = float((q.strike - spot) / spot)
        return (abs(otm - cfg.target_cc_otm_pct), -float(q.mid))
    candidates.sort(key=_key)
    return candidates[0]


# ---------- Score ----------

def _score(
    *,
    annualized_yield: float,
    otm_pct: float,
    open_interest: int,
    spread_pct: float | None,
    earnings_before_expiry: bool,
    composite_score: float,
    cfg: WheelScanConfig,
) -> tuple[Decimal, dict]:
    """Composite 0-100 ranking score.

    Reward: higher annualized yield, more OTM cushion, higher liquidity,
            higher underlying quality.
    Penalize: very high yields (likely vol crush coming), wide spreads,
              earnings inside the holding window.
    """
    # Yield reward (saturating)
    y = max(0.0, min(annualized_yield, cfg.max_annualized_yield))
    # Bell-shape around target band — peak at ~17% annualized
    target_y = 0.17
    y_score = max(0.0, 1.0 - abs(y - target_y) / target_y)

    # Cushion reward — more OTM is safer
    cushion_score = min(1.0, max(0.0, (otm_pct - 0.02) / 0.13))

    # Liquidity — open interest log-curve
    liq_score = min(1.0, math.log10(max(open_interest, 1)) / 3.0)

    # Spread penalty (tight is better)
    if spread_pct is None:
        spread_score = 0.5
    else:
        spread_score = max(0.0, 1.0 - spread_pct / cfg.max_bid_ask_spread_pct)

    # Quality of underlying
    quality_score = min(1.0, max(0.0, composite_score))

    # Earnings binary: -0.15 if earnings inside window
    earnings_score = -0.15 if earnings_before_expiry else 0.0

    weighted = (
        0.30 * y_score
        + 0.20 * cushion_score
        + 0.15 * liq_score
        + 0.10 * spread_score
        + 0.25 * quality_score
        + earnings_score
    )
    out = max(0.0, min(1.0, weighted)) * 100
    breakdown = {
        "yield":     round(y_score, 3),
        "cushion":   round(cushion_score, 3),
        "liquidity": round(liq_score, 3),
        "spread":    round(spread_score, 3),
        "quality":   round(quality_score, 3),
        "earnings_penalty": round(earnings_score, 3),
    }
    return Decimal(str(round(out, 2))), breakdown


# ---------- Owned positions (for CC suggestions) ----------

async def _owned_symbols(session: AsyncSession, user_id: str) -> dict[str, dict]:
    """Return {symbol: {qty, avg_cost, currency}} for the user's open long positions."""
    q = (
        select(Position, Account)
        .join(Account, Position.account_id == Account.id)
        .where(Account.user_id == user_id, Position.quantity > 0)
    )
    out: dict[str, dict] = {}
    for pos, _acct in (await session.execute(q)).all():
        # Stocks held in 100-lot multiples can be covered-called.
        if int(pos.quantity) < 100:
            continue
        out[pos.symbol] = {
            "qty": int(pos.quantity),
            "avg_cost": float(pos.avg_cost),
            "currency": pos.currency,
        }
    return out


# ---------- Earnings lookup ----------

async def _earnings_map(session: AsyncSession, symbols: list[str]) -> dict[str, datetime]:
    if not symbols:
        return {}
    q = select(EarningsDate).where(EarningsDate.symbol.in_(symbols))
    out: dict[str, datetime] = {}
    for r in (await session.execute(q)).scalars().all():
        if r.next_earnings_date is not None:
            out[r.symbol] = r.next_earnings_date
    return out


# ---------- Main scan ----------

async def scan_wheel(
    session: AsyncSession,
    user_id: str,
    cfg: WheelScanConfig | None = None,
) -> dict:
    """Run a wheel scan for the given user. Returns summary stats.

    Rewrites the `wheel_candidates` table for this user.
    """
    cfg = cfg or WheelScanConfig()
    started = datetime.now(timezone.utc)

    quality = await _quality_candidates(session, cfg)
    if not quality:
        log.info("wheel scan: no quality candidates")
        return {"scanned": 0, "with_data": 0, "candidates": 0, "started_at": started.isoformat()}

    symbols = [s.symbol for s in quality]
    owned = await _owned_symbols(session, user_id)
    # Also scan owned symbols that may not have made the quality cut, for CC suggestions.
    for sym in owned:
        if sym not in symbols:
            symbols.append(sym)

    earnings_by_sym = await _earnings_map(session, symbols)
    score_by_sym = {s.symbol: s for s in quality}

    rows: list[dict] = []
    scanned = 0
    with_data = 0

    # yfinance is sync — run chain fetches with limited concurrency so we don't
    # melt our HTTP pool.
    sem = asyncio.Semaphore(8)

    async def _one(sym: str):
        nonlocal scanned, with_data
        async with sem:
            scanned += 1
            expiries = await list_expiries(sym)
            target = pick_expiry_near_dte(expiries, cfg.target_dte, cfg.dte_tolerance)
            if target is None:
                return
            chain = await fetch_chain(sym, target)
            if chain is None or (not chain.puts and not chain.calls):
                return
            with_data += 1

            score_row = score_by_sym.get(sym)
            spot = Decimal(str(score_row.last_close)) if (score_row and score_row.last_close) else None
            sector = score_row.sector if score_row else None
            composite = float(score_row.composite_score) if (score_row and score_row.composite_score) else 0.0
            if spot is None or spot <= 0:
                # No spot from screener — try inferring from chain midpoint
                # (skip — too unreliable for wheel decisions)
                return

            dte = days_to_expiry(target)
            next_e = earnings_by_sym.get(sym)
            earnings_in_window = bool(
                next_e is not None
                and next_e.date() >= datetime.now(timezone.utc).date()
                and next_e.date() <= target
            )

            # CSP
            csp = _pick_best_csp(chain.puts, spot, cfg)
            if csp is not None:
                premium = csp.mid
                capital = csp.strike * 100  # cash secured = strike * 100
                yield_pct = premium / csp.strike            # premium / strike (per-share basis)
                ann = yield_pct * Decimal(365) / Decimal(max(dte, 1))
                otm = (spot - csp.strike) / spot
                spread = _spread_pct(csp)
                score, breakdown = _score(
                    annualized_yield=float(ann),
                    otm_pct=float(otm),
                    open_interest=csp.open_interest,
                    spread_pct=float(spread) if spread is not None else None,
                    earnings_before_expiry=earnings_in_window,
                    composite_score=composite,
                    cfg=cfg,
                )
                # Hide candidates whose annualized yield is wildly outside the user's band
                # (still keep min as soft floor — we'll filter on the read side too).
                if float(ann) <= cfg.max_annualized_yield:
                    rows.append({
                        "id": uuid.uuid4(),
                        "user_id": user_id,
                        "symbol": sym,
                        "sector": sector,
                        "strategy": "csp",
                        "last_price": spot,
                        "expiry": datetime.combine(target, datetime.min.time()),
                        "dte": dte,
                        "strike": csp.strike,
                        "option_type": "put",
                        "bid": csp.bid,
                        "ask": csp.ask,
                        "mid": csp.mid,
                        "last": csp.last,
                        "bid_ask_spread_pct": spread,
                        "open_interest": csp.open_interest,
                        "volume": csp.volume,
                        "implied_volatility": csp.implied_volatility,
                        "delta_approx": _approx_delta_csp(csp.strike, spot, dte, csp.implied_volatility),
                        "premium_yield_pct": yield_pct,
                        "annualized_yield_pct": ann,
                        "otm_pct": otm,
                        "capital_at_risk": capital,
                        "breakeven": csp.strike - csp.mid,
                        "earnings_before_expiry": earnings_in_window,
                        "next_earnings_date": next_e,
                        "score": score,
                        "score_breakdown": breakdown,
                        "scanned_at": started,
                    })

            # CC — only if the user actually holds the underlying in 100-lots
            owned_info = owned.get(sym)
            if owned_info is not None:
                cc = _pick_best_cc(chain.calls, spot, cfg)
                if cc is not None:
                    premium = cc.mid
                    capital = spot * 100  # market value of 100 shares
                    yield_pct = premium / spot
                    ann = yield_pct * Decimal(365) / Decimal(max(dte, 1))
                    otm = (cc.strike - spot) / spot
                    spread = _spread_pct(cc)
                    score, breakdown = _score(
                        annualized_yield=float(ann),
                        otm_pct=float(otm),
                        open_interest=cc.open_interest,
                        spread_pct=float(spread) if spread is not None else None,
                        earnings_before_expiry=earnings_in_window,
                        composite_score=composite,
                        cfg=cfg,
                    )
                    if float(ann) <= cfg.max_annualized_yield:
                        rows.append({
                            "id": uuid.uuid4(),
                            "user_id": user_id,
                            "symbol": sym,
                            "sector": sector,
                            "strategy": "cc",
                            "last_price": spot,
                            "expiry": datetime.combine(target, datetime.min.time()),
                            "dte": dte,
                            "strike": cc.strike,
                            "option_type": "call",
                            "bid": cc.bid,
                            "ask": cc.ask,
                            "mid": cc.mid,
                            "last": cc.last,
                            "bid_ask_spread_pct": spread,
                            "open_interest": cc.open_interest,
                            "volume": cc.volume,
                            "implied_volatility": cc.implied_volatility,
                            "delta_approx": _approx_delta_cc(cc.strike, spot, dte, cc.implied_volatility),
                            "premium_yield_pct": yield_pct,
                            "annualized_yield_pct": ann,
                            "otm_pct": otm,
                            "capital_at_risk": capital,
                            "breakeven": spot - cc.mid,
                            "earnings_before_expiry": earnings_in_window,
                            "next_earnings_date": next_e,
                            "score": score,
                            "score_breakdown": breakdown,
                            "scanned_at": started,
                        })

    await asyncio.gather(*[_one(s) for s in symbols])

    # Wipe prior candidates for this user, write new ones.
    await session.execute(delete(WheelCandidate).where(WheelCandidate.user_id == user_id))
    if rows:
        session.add_all([WheelCandidate(**r) for r in rows])
    await session.commit()

    finished = datetime.now(timezone.utc)
    return {
        "scanned": scanned,
        "with_data": with_data,
        "candidates": len(rows),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
    }
