"""Screener pipeline orchestrator.

Two modes:
  auto   — pulls S&P 500 + S&P 400 + S&P 600 + NASDAQ 100 + TSX 60
  manual — only scores symbols explicitly in the watchlist

Pipeline (auto mode):
  1. Universe fetch    — Wikipedia → upsert screener_symbols
  2. EOD download      — incremental (delta for existing, full 2yr for new)
  3. TT pre-filter     — discard TT < 3 to avoid scoring everything deeply
  4. VCP scoring       — on TT-passing subset only
  5. yfinance fundamentals — earningsQuarterlyGrowth, revenueGrowth, ROE, margins
                             covers ALL scored symbols including Canadian stocks
  6. RS rank           — percentile within screener universe vs SPY
  7. Composite score   — TT 25% + VCP 25% + RS 20% + Fundamentals 30%
  8. Persist results
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScreenerScore, ScreenerSymbol
from app.services.eod_service import BENCHMARKS, get_bars_df, sync_eod_incremental
from app.services.trend_template import MIN_BARS, score_trend_template
from app.services.universe_service import build_universe
from app.services.vcp_scorer import score_vcp

log = logging.getLogger(__name__)

TT_PREFILTER_THRESHOLD = 3    # symbols below this TT score are dropped
YF_FUNDAMENTALS_CONCURRENCY = 12  # concurrent yfinance info calls


async def _fetch_yf_fundamentals_batch(symbols: list[str]) -> dict[str, dict]:
    """Fetch earningsQuarterlyGrowth, revenueGrowth, ROE, margins from yfinance.

    Uses yfinance ticker.info — covers ALL symbols including Canadian (TSX).
    earningsQuarterlyGrowth = most recent quarter's YoY EPS growth (Minervini's #1 metric).
    Runs concurrently; much faster than EDGAR for the screener use case.
    """
    sem = asyncio.Semaphore(YF_FUNDAMENTALS_CONCURRENCY)
    results: dict[str, dict] = {}

    def _info_sync(sym: str) -> dict:
        try:
            info = yf.Ticker(sym).info or {}
            eps_growth = info.get("earningsQuarterlyGrowth")
            if eps_growth is None:
                eps_growth = info.get("earningsGrowth")
            return {
                "net_income_growth": eps_growth,
                "revenue_growth":    info.get("revenueGrowth"),
                "net_margin":        info.get("profitMargins"),
                "roe":               info.get("returnOnEquity"),
                "sector":            info.get("sector"),
                "trailing_eps":      info.get("trailingEps"),
            }
        except Exception:
            return {}

    async def _fetch_one(sym: str) -> None:
        async with sem:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _info_sync, sym)
            if data:
                results[sym] = data

    await asyncio.gather(*[_fetch_one(s) for s in symbols])
    return results


@dataclass
class PipelineStats:
    universe_size: int = 0
    eod_downloaded: int = 0
    liquidity_filtered: int = 0   # skipped due to avg volume < 100k
    tt_passing: int = 0
    scored: int = 0
    with_fundamentals: int = 0


async def run_screener(
    session: AsyncSession,
    *,
    mode: str = "auto",    # "auto" | "manual"
) -> tuple[list[ScreenerScore], PipelineStats]:
    """Run full screener pipeline. Returns (results, stats)."""
    stats = PipelineStats()

    # ── Step 1: Universe ──────────────────────────────────────────────────────
    if mode == "auto":
        universe_counts = await build_universe(session)
        log.info("Universe built: %s", universe_counts)

    sym_result = await session.execute(
        select(ScreenerSymbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
    )
    all_sym_rows = sym_result.scalars().all()
    stats.universe_size = len(all_sym_rows)

    if not all_sym_rows:
        return [], stats

    all_symbols = [s.symbol for s in all_sym_rows]
    sym_row_map = {s.symbol: s for s in all_sym_rows}

    # ── Step 2: Incremental EOD download ─────────────────────────────────────
    eod_symbols = list(set(all_symbols) | set(BENCHMARKS))
    counts = await sync_eod_incremental(session, eod_symbols)
    stats.eod_downloaded = sum(v for v in counts.values() if v > 0)
    log.info("EOD download: %d symbols, %d bars upserted", len(counts), stats.eod_downloaded)

    # ── Step 3: TT pre-filter + liquidity screen ─────────────────────────────
    # Minervini requires stocks to be liquid enough to trade without moving the
    # market. We filter out names with <100k shares/day avg volume.
    MIN_AVG_VOLUME = 100_000
    spy_df = await get_bars_df(session, "SPY", days=504)

    tt_results = {}
    for sym in all_symbols:
        df = await get_bars_df(session, sym, days=MIN_BARS + 30)
        if df.empty or len(df) < MIN_BARS:
            continue
        # Liquidity check: 20-day average volume
        avg_vol = df["volume"].tail(20).mean()
        if avg_vol < MIN_AVG_VOLUME:
            log.debug("%s avg vol %.0f < %d — skipped (illiquid)", sym, avg_vol, MIN_AVG_VOLUME)
            continue
        tt = score_trend_template(df, benchmark_df=spy_df if not spy_df.empty else None)
        tt_results[sym] = (df, tt)

    passing = [(sym, df, tt) for sym, (df, tt) in tt_results.items()
               if tt.score >= TT_PREFILTER_THRESHOLD]
    stats.tt_passing = len(passing)
    log.info("TT pre-filter: %d / %d symbols passed (TT >= %d)",
             len(passing), len(all_symbols), TT_PREFILTER_THRESHOLD)

    # ── Step 4: VCP scoring ──────────────────────────────────────────────────
    # Download 2yr for VCP (needs 90-day base analysis)
    vcp_symbols = [sym for sym, _, _ in passing]
    if vcp_symbols:
        vcp_counts = await sync_eod_incremental(session, vcp_symbols, full_years=2)
        log.info("VCP EOD top-up: %d symbols", len(vcp_counts))

    scored: list[tuple[str, any, any, any]] = []  # (sym, df2yr, tt, vcp)
    for sym, _df1y, tt in passing:
        df2yr = await get_bars_df(session, sym, days=504)
        if df2yr.empty:
            continue
        # Re-run TT on full 2yr data for accuracy
        tt_full = score_trend_template(df2yr, benchmark_df=spy_df if not spy_df.empty else None)
        vcp = score_vcp(df2yr, tt_full)
        scored.append((sym, df2yr, tt_full, vcp))

    stats.scored = len(scored)

    # ── Step 5: yfinance fundamentals for ALL scored symbols ─────────────────
    # Using yfinance ticker.info instead of EDGAR — covers Canadian stocks too,
    # 10× faster, and earningsQuarterlyGrowth is the Minervini key metric.
    all_scored_symbols = [sym for sym, _, _, _ in scored]
    log.info("Fetching yfinance fundamentals for %d symbols", len(all_scored_symbols))
    fundamental_map = await _fetch_yf_fundamentals_batch(all_scored_symbols)
    stats.with_fundamentals = sum(1 for v in fundamental_map.values() if v)

    # ── Step 6: Upsert screener_scores ───────────────────────────────────────
    existing_result = await session.execute(select(ScreenerScore))
    existing_scores = {s.symbol: s for s in existing_result.scalars().all()}

    result_rows: list[ScreenerScore] = []
    for sym, df, tt, vcp in scored:
        sym_row = sym_row_map.get(sym)

        score_row = existing_scores.get(sym)
        if score_row is None:
            score_row = ScreenerScore(symbol=sym)
            session.add(score_row)

        score_row.scored_at = datetime.now(timezone.utc)
        score_row.universe_source = (sym_row.notes or "").split(":")[0] if sym_row else None
        score_row.tt_score = tt.score
        score_row.tt_criteria = {k: bool(v) for k, v in tt.criteria.items()}
        score_row.vcp_score = Decimal(str(vcp.score))
        score_row.vcp_details = {
            **vcp.details,
            "base_depth_pct": vcp.base_depth_pct,
            "atr_ratio": vcp.atr_ratio,
            "volume_ratio": vcp.volume_ratio,
        }
        score_row.rs_raw = Decimal(str(tt.rs_raw)) if tt.rs_raw is not None else None
        score_row.last_close = Decimal(str(tt.last_close)) if tt.last_close else None
        score_row.ma_50  = Decimal(str(tt.ma_50))  if tt.ma_50  else None
        score_row.ma_150 = Decimal(str(tt.ma_150)) if tt.ma_150 else None
        score_row.ma_200 = Decimal(str(tt.ma_200)) if tt.ma_200 else None
        score_row.high_52w = Decimal(str(tt.high_52w)) if tt.high_52w else None
        score_row.low_52w  = Decimal(str(tt.low_52w))  if tt.low_52w  else None

        fund = fundamental_map.get(sym)
        if fund:
            rev_g  = fund.get("revenue_growth")
            ni_g   = fund.get("net_income_growth")
            margin = fund.get("net_margin")
            roe    = fund.get("roe")
            eps    = fund.get("trailing_eps")
            sector = fund.get("sector")

            # Minervini 4-point fundamental score
            score_pts = 0
            if ni_g   is not None and ni_g   >= 0.25: score_pts += 1
            if rev_g  is not None and rev_g  >= 0.15: score_pts += 1
            if margin is not None and margin >= 0.10: score_pts += 1
            if roe    is not None and roe    >= 0.17: score_pts += 1

            score_row.fundamental_score  = Decimal(str(round(score_pts / 4.0, 3)))
            score_row.revenue_growth     = Decimal(str(round(rev_g,  4))) if rev_g  is not None else None
            score_row.net_income_growth  = Decimal(str(round(ni_g,   4))) if ni_g   is not None else None
            score_row.net_margin         = Decimal(str(round(margin, 4))) if margin is not None else None
            score_row.eps_ttm            = Decimal(str(round(eps,    4))) if eps    is not None else None
            score_row.sector             = sector or score_row.sector
            score_row.fundamental_error  = None
        else:
            score_row.fundamental_score = Decimal(0)

        result_rows.append(score_row)

    await session.flush()

    # ── Step 7: RS rank (percentile within universe) ─────────────────────────
    rs_vals = [(s, float(s.rs_raw)) for s in result_rows if s.rs_raw is not None]
    rs_vals.sort(key=lambda x: x[1])
    n = len(rs_vals)
    for rank, (s, _) in enumerate(rs_vals):
        s.rs_rank = int((rank / max(n - 1, 1)) * 99) if n > 1 else 50

    # ── Step 8: Composite score ───────────────────────────────────────────────
    for s in result_rows:
        tt_norm   = s.tt_score / 8.0
        vcp_norm  = float(s.vcp_score)
        rs_norm   = (s.rs_rank or 50) / 99.0
        fund_norm = float(s.fundamental_score)
        composite = tt_norm * 0.25 + vcp_norm * 0.25 + rs_norm * 0.20 + fund_norm * 0.30
        s.composite_score = Decimal(str(round(composite, 3)))

    await session.commit()
    result_rows.sort(key=lambda s: float(s.composite_score), reverse=True)
    return result_rows, stats


async def get_screener_results(
    session: AsyncSession,
    *,
    min_tt: int = 0,
    min_vcp: float = 0.0,
    sector: str | None = None,
) -> list[ScreenerScore]:
    q = select(ScreenerScore).order_by(ScreenerScore.composite_score.desc())
    result = await session.execute(q)
    rows = result.scalars().all()

    # Apply filters in Python (simple enough, avoids dynamic SQLAlchemy)
    if min_tt:
        rows = [r for r in rows if r.tt_score >= min_tt]
    if min_vcp:
        rows = [r for r in rows if float(r.vcp_score) >= min_vcp]
    if sector:
        rows = [r for r in rows if (r.sector or "").lower() == sector.lower()]

    return rows
