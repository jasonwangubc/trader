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
  7. EPS rank / SMR rank — IBD-style 0-99 percentile ranks within universe
  8. Composite score   — TT 15% + VCP 20% + RS 25% + EPS 25% + SMR 15%
  9. Persist results
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

import numpy as np
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScreenerScore, ScreenerSymbol
from app.services.eod_service import BENCHMARKS, get_bars_df, sync_eod_incremental
from app.services.pattern_service import detect_pattern
from app.services.trend_template import MIN_BARS, score_trend_template
from app.services.universe_service import build_universe
from app.services.vcp_scorer import score_vcp

log = logging.getLogger(__name__)

# Pre-filter: require the THREE essential Stage-2 criteria, not "any 3 of 8".
# These three together establish: "this stock is in a real long-term uptrend,
# near its highs, and tradable for longs." A stock that fails any of these is
# not a long candidate, full stop — regardless of how many other criteria pass.
#
#   price_above_150_200   — Stage 2 floor (price above the major MAs)
#   ma_200_trending_up    — confirms long-term uptrend, not a dead-cat bounce
#   within_25pct_52w_high — confirms proximity to highs (the trade-zone)
ESSENTIAL_TT_CRITERIA: frozenset[str] = frozenset({
    "price_above_150_200",
    "ma_200_trending_up",
    "within_25pct_52w_high",
})

# Legacy threshold — kept for backward compatibility but no longer used for the
# primary filter. Symbols with high TT scores that miss an essential criterion
# are still rejected.
TT_PREFILTER_THRESHOLD = 3

# yfinance Ticker.info is rate-limited aggressively. Keep concurrency low,
# add a per-call delay, and retry once with backoff before giving up.
# At 3 concurrent + 0.4s delay, a 1,000-symbol batch takes ~2-3 minutes
# rather than hammering and failing in 30s.
YF_FUNDAMENTALS_CONCURRENCY = 3
YF_FUNDAMENTALS_DELAY = 0.4       # seconds between each completed call
YF_FUNDAMENTALS_RETRY_DELAY = 5.0 # seconds before retry on exception
YF_FUNDAMENTALS_STALE_DAYS = 5    # re-fetch fundamentals after this many days


def _yf_info_sync(sym: str) -> dict:
    """Fetch yfinance Ticker.info for one symbol with one retry on failure.

    Returns an empty dict on permanent failure or if the response lacks price
    data (which usually means the symbol isn't in yfinance at all).

    We fetch BOTH quarterly and annual EPS growth so we can detect acceleration:
      - earningsQuarterlyGrowth: most recent quarter YoY (the CANSLIM "C")
      - earningsGrowth: TTM/annual YoY (baseline)
    If quarterly > annual, earnings are accelerating — a Minervini/CANSLIM signal.
    """
    import time
    for attempt in range(2):
        try:
            info = yf.Ticker(sym).info or {}
            # An empty or stub response has no regularMarketPrice. Skip it.
            if not info.get("regularMarketPrice") and not info.get("currentPrice") and not info.get("trailingEps"):
                if attempt == 0:
                    time.sleep(YF_FUNDAMENTALS_RETRY_DELAY)
                    continue
                return {}
            quarterly_growth = info.get("earningsQuarterlyGrowth")
            annual_growth    = info.get("earningsGrowth")
            # Prefer quarterly for the primary metric; fall back to annual
            eps_growth = quarterly_growth if quarterly_growth is not None else annual_growth
            return {
                "net_income_growth":     eps_growth,
                "earnings_annual_growth": annual_growth,
                "revenue_growth":        info.get("revenueGrowth"),
                "net_margin":            info.get("profitMargins"),
                "roe":                   info.get("returnOnEquity"),
                "sector":                info.get("sector"),
                "trailing_eps":          info.get("trailingEps"),
            }
        except Exception as exc:
            if attempt == 0:
                log.debug("yfinance info failed for %s (%s), retrying in %.0fs", sym, exc, YF_FUNDAMENTALS_RETRY_DELAY)
                time.sleep(YF_FUNDAMENTALS_RETRY_DELAY)
            else:
                log.debug("yfinance info failed for %s after retry: %s", sym, exc)
    return {}


async def _fetch_yf_fundamentals_batch_slow(
    symbols: list[str],
    on_done: Callable[[], None] | None = None,
) -> dict[str, dict]:
    """Slow retry pass for symbols that failed the first yfinance attempt.

    Uses concurrency=1 and 1.5s between calls — much gentler on the rate
    limiter. Slower per-symbol, but successfully gets data that the fast
    pass missed due to rate-limiting.
    """
    sem = asyncio.Semaphore(1)
    results: dict[str, dict] = {}

    async def _fetch_one(sym: str) -> None:
        async with sem:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _yf_info_sync, sym)
            if data:
                results[sym] = data
            await asyncio.sleep(1.5)   # gentle pacing
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass

    await asyncio.gather(*[_fetch_one(s) for s in symbols])
    return results


async def _fetch_edgar_fundamentals(
    symbol_cik_pairs: list[tuple[str, str]],
    on_done: Callable[[], None] | None = None,
) -> dict[str, dict]:
    """Final fallback: pull fundamentals from SEC EDGAR's free companyfacts API.

    EDGAR is more reliable than yfinance but only covers US-registered companies.
    Returns dicts shaped like _fetch_yf_fundamentals so they merge cleanly.
    """
    from app.services.edgar_fundamentals import fetch_fundamentals

    sem = asyncio.Semaphore(5)
    results: dict[str, dict] = {}

    async def _fetch_one(sym: str, cik: str) -> None:
        async with sem:
            try:
                snap = await fetch_fundamentals(cik, sym)
                if not snap.error and (snap.revenue_growth is not None or snap.net_income_growth is not None):
                    results[sym] = {
                        "net_income_growth": snap.net_income_growth,
                        "revenue_growth":    snap.revenue_growth,
                        "net_margin":        snap.net_margin,
                        "roe":               None,           # EDGAR snapshot doesn't compute ROE
                        "sector":            None,
                        "trailing_eps":      snap.eps_ttm,
                    }
            except Exception as exc:
                log.debug("EDGAR fallback failed for %s: %s", sym, exc)
            await asyncio.sleep(0.12)   # respect SEC ≤10 req/sec
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass

    await asyncio.gather(*[_fetch_one(s, c) for s, c in symbol_cik_pairs])
    return results


async def _fetch_yf_fundamentals_batch(
    symbols: list[str],
    on_done: Callable[[], None] | None = None,
) -> dict[str, dict]:
    """Fetch earningsQuarterlyGrowth, revenueGrowth, ROE, margins from yfinance.

    Uses yfinance ticker.info — covers ALL symbols including Canadian (TSX).
    earningsQuarterlyGrowth = most recent quarter's YoY EPS growth (Minervini's #1 metric).

    Rate-limiting strategy: low concurrency (3) + per-call delay + retry with
    backoff inside _yf_info_sync. Slower than hammering, but actually completes.

    on_done: optional callback fired once per completed symbol (success OR
    failure), used for the progress bar.
    """
    sem = asyncio.Semaphore(YF_FUNDAMENTALS_CONCURRENCY)
    results: dict[str, dict] = {}

    async def _fetch_one(sym: str) -> None:
        async with sem:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _yf_info_sync, sym)
            if data:
                results[sym] = data
            # Pace requests even after the semaphore releases
            await asyncio.sleep(YF_FUNDAMENTALS_DELAY)
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass

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


# Stage weights are rough — they describe *typical* time share so the overall
# pct progress feels accurate. Universe + ranks are fast; fundamentals are slow.
_STAGE_WEIGHTS: list[tuple[str, str, float]] = [
    ("universe",     "Building universe",         0.03),
    ("eod",          "Downloading price data",    0.25),
    ("tt",           "Scoring trend template",    0.18),
    ("vcp",          "Scoring VCP setups",        0.10),
    ("fundamentals", "Fetching fundamentals",     0.35),
    ("rank",         "Computing rankings",        0.04),
    ("persist",      "Saving results",            0.05),
]
_STAGE_INDEX = {key: i for i, (key, _, _) in enumerate(_STAGE_WEIGHTS)}


@dataclass
class ScreenerProgress:
    """Live progress snapshot emitted by `run_screener`. Used by the API status
    endpoint to drive a progress bar in the UI.

    Update granularity:
      - Stage transitions always update.
      - Long stages (TT loop, VCP loop, fundamentals batch) tick per-symbol.
      - Short stages (universe, ranks, persist) update only at boundaries.
    """
    stage_key: str = "idle"
    stage_label: str = ""
    stage_index: int = 0
    total_stages: int = len(_STAGE_WEIGHTS)
    processed: int = 0
    total: int = 0
    started_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    stats: dict = field(default_factory=dict)

    def begin(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self.updated_at = self.started_at
        self.stage_key = "starting"
        self.stage_label = "Starting…"

    def begin_stage(self, key: str, total: int = 0) -> None:
        idx = _STAGE_INDEX.get(key)
        if idx is None:
            return
        self.stage_key = key
        self.stage_label = _STAGE_WEIGHTS[idx][1]
        self.stage_index = idx
        self.processed = 0
        self.total = total
        self.updated_at = datetime.now(timezone.utc)

    def tick(self, n: int = 1) -> None:
        self.processed += n
        self.updated_at = datetime.now(timezone.utc)

    def set_processed(self, n: int) -> None:
        self.processed = n
        self.updated_at = datetime.now(timezone.utc)

    def finish(self, stats: dict | None = None) -> None:
        if stats:
            self.stats = stats
        self.stage_key = "done"
        self.stage_label = "Done"
        self.stage_index = self.total_stages
        self.processed = self.total or self.processed
        self.finished_at = datetime.now(timezone.utc)
        self.updated_at = self.finished_at

    def fail(self, msg: str) -> None:
        self.stage_key = "error"
        self.stage_label = "Failed"
        self.error = msg
        self.finished_at = datetime.now(timezone.utc)
        self.updated_at = self.finished_at

    def overall_pct(self) -> float:
        """Weighted overall completion percent (0-100). Uses configured stage
        weights so the bar reflects real time share, not just stage count."""
        if self.stage_key == "done":
            return 100.0
        if self.stage_key in ("idle", "starting", "error"):
            return 0.0
        completed_weight = sum(w for (_, _, w) in _STAGE_WEIGHTS[: self.stage_index])
        current_w = _STAGE_WEIGHTS[self.stage_index][2] if self.stage_index < self.total_stages else 0.0
        within = (self.processed / self.total) if self.total > 0 else 0.0
        within = max(0.0, min(within, 1.0))
        return (completed_weight + current_w * within) * 100.0


async def run_screener(
    session: AsyncSession,
    *,
    mode: str = "auto",    # "auto" | "manual"
    progress: ScreenerProgress | None = None,
) -> tuple[list[ScreenerScore], PipelineStats]:
    """Run full screener pipeline. Returns (results, stats).

    If `progress` is supplied, the pipeline updates it after each stage and
    per-symbol within long stages (TT scoring, VCP scoring, fundamentals)
    so the API can drive a live progress bar.
    """
    stats = PipelineStats()
    p = progress  # alias

    # ── Step 1: Universe ──────────────────────────────────────────────────────
    if p: p.begin_stage("universe")
    if mode == "auto":
        universe_counts = await build_universe(session)
        log.info("Universe built: %s", universe_counts)

    sym_result = await session.execute(
        select(ScreenerSymbol).where(ScreenerSymbol.is_active == True)  # noqa: E712
    )
    all_sym_rows = sym_result.scalars().all()
    stats.universe_size = len(all_sym_rows)
    if p: p.set_processed(len(all_sym_rows))

    if not all_sym_rows:
        if p: p.finish(stats={"universe_size": 0})
        return [], stats

    # Deduplicate while preserving order — the universe can have the same
    # ticker on multiple exchange/CIK entries which causes unique-constraint
    # violations when both hit the persist loop.
    seen: set[str] = set()
    all_symbols = [s.symbol for s in all_sym_rows if not (s.symbol in seen or seen.add(s.symbol))]  # type: ignore[func-returns-value]
    sym_row_map = {s.symbol: s for s in all_sym_rows}

    # ── Step 2: Incremental EOD download ─────────────────────────────────────
    eod_symbols = list(set(all_symbols) | set(BENCHMARKS))
    if p: p.begin_stage("eod", total=len(eod_symbols))
    counts = await sync_eod_incremental(
        session,
        eod_symbols,
        on_chunk=(p.tick if p else None),
    )
    stats.eod_downloaded = sum(v for v in counts.values() if v > 0)
    log.info("EOD download: %d symbols, %d bars upserted", len(counts), stats.eod_downloaded)

    # ── Load existing scores early — used for session-resume caching ─────────
    # We fetch all existing ScreenerScore rows before the TT loop so we can:
    # (a) skip symbols already scored in the current session (resume after sleep)
    # (b) reuse fundamentals that are still fresh (< YF_FUNDAMENTALS_STALE_DAYS old)
    existing_result = await session.execute(select(ScreenerScore))
    existing_scores = {s.symbol: s for s in existing_result.scalars().all()}

    # Session boundary: midnight UTC today. Symbols scored after this point were
    # completed in the current session run and can be trusted as-is.
    session_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    cached_rows: list[ScreenerScore] = []
    symbols_to_score: list[str] = []
    for sym in all_symbols:
        row = existing_scores.get(sym)
        if row is not None and row.scored_at is not None and row.scored_at >= session_start:
            cached_rows.append(row)
        else:
            symbols_to_score.append(sym)

    if cached_rows:
        log.info(
            "Session resume: %d symbols already scored today, scoring %d fresh",
            len(cached_rows), len(symbols_to_score),
        )

    # ── Step 3: TT pre-filter + liquidity screen ─────────────────────────────
    # Minervini requires stocks to be liquid enough to trade without moving the
    # market. We filter out names with <100k shares/day avg volume.
    if p:
        p.begin_stage("tt", total=len(symbols_to_score))
        if cached_rows:
            p.stage_label = f"Scoring trend template ({len(cached_rows)} resuming from today)"
    MIN_AVG_VOLUME = 100_000
    spy_df = await get_bars_df(session, "SPY", days=504)

    tt_results = {}
    for sym in symbols_to_score:
        df = await get_bars_df(session, sym, days=MIN_BARS + 30)
        if df.empty or len(df) < MIN_BARS:
            if p: p.tick()
            continue
        # Liquidity check: 20-day average volume
        avg_vol = df["volume"].tail(20).mean()
        if avg_vol < MIN_AVG_VOLUME:
            log.debug("%s avg vol %.0f < %d — skipped (illiquid)", sym, avg_vol, MIN_AVG_VOLUME)
            if p: p.tick()
            continue
        tt = score_trend_template(df, benchmark_df=spy_df if not spy_df.empty else None)
        tt_results[sym] = (df, tt)
        if p: p.tick()

    # Essential-criteria filter: every stock must pass ALL three core Stage-2
    # criteria. This is stricter than "any 3 of 8" — a stock can have TT=4
    # and still be rejected if it lacks one essential criterion.
    def _passes_essentials(tt) -> bool:
        return all(tt.criteria.get(c) for c in ESSENTIAL_TT_CRITERIA)

    passing = [(sym, df, tt) for sym, (df, tt) in tt_results.items()
               if _passes_essentials(tt)]
    stats.tt_passing = len(passing) + sum(
        1 for r in cached_rows
        if r.tt_criteria and all(r.tt_criteria.get(c) for c in ESSENTIAL_TT_CRITERIA)
    )
    log.info(
        "TT essentials filter: %d fresh passed (out of %d scored), %d cached carried forward",
        len(passing), len(tt_results), len(cached_rows),
    )

    # ── Step 4: VCP scoring ──────────────────────────────────────────────────
    # Download 2yr for VCP (needs 90-day base analysis) — bulk, no sub-ticks.
    if p:
        p.begin_stage("vcp", total=len(passing))
        if cached_rows:
            p.stage_label = f"Scoring VCP ({len(cached_rows)} cached, scoring {len(passing)} fresh)"
    vcp_symbols = [sym for sym, _, _ in passing]
    if vcp_symbols:
        # VCP top-up: no progress ticking here — the VCP scoring loop below
        # provides per-symbol ticks once this bulk download finishes.
        vcp_counts = await sync_eod_incremental(session, vcp_symbols, full_years=2)
        log.info("VCP EOD top-up: %d symbols", len(vcp_counts))

    scored: list[tuple[str, any, any, any]] = []  # (sym, df2yr, tt, vcp)
    for sym, _df1y, tt in passing:
        df2yr = await get_bars_df(session, sym, days=504)
        if df2yr.empty:
            if p: p.tick()
            continue
        # Re-run TT on full 2yr data for accuracy
        tt_full = score_trend_template(df2yr, benchmark_df=spy_df if not spy_df.empty else None)
        vcp = score_vcp(df2yr, tt_full)
        scored.append((sym, df2yr, tt_full, vcp))
        if p: p.tick()

    stats.scored = len(scored) + len(cached_rows)

    # ── Step 5: yfinance fundamentals — skip symbols with fresh data ─────────
    # Cached symbols (scored earlier today) skip fundamentals entirely.
    # Non-cached symbols check YF_FUNDAMENTALS_STALE_DAYS before hitting yfinance.
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=YF_FUNDAMENTALS_STALE_DAYS)
    all_scored_symbols = [sym for sym, _, _, _ in scored]

    fresh_fund: dict[str, dict] = {}
    for sym in all_scored_symbols:
        row = existing_scores.get(sym)
        if (
            row is not None
            and row.scored_at is not None
            and row.scored_at >= stale_cutoff
            and row.fundamental_score is not None
            and float(row.fundamental_score) > 0
        ):
            fresh_fund[sym] = {
                "net_income_growth": float(row.net_income_growth) if row.net_income_growth is not None else None,
                "revenue_growth":    float(row.revenue_growth)    if row.revenue_growth    is not None else None,
                "net_margin":        float(row.net_margin)        if row.net_margin        is not None else None,
                "roe":               float(row.roe)               if row.roe               is not None else None,
                "sector":            row.sector,
                "trailing_eps":      float(row.eps_ttm)           if row.eps_ttm           is not None else None,
            }

    symbols_needing_fetch = [s for s in all_scored_symbols if s not in fresh_fund]
    log.info(
        "Fundamentals: %d fresh (cached), %d need yfinance fetch",
        len(fresh_fund), len(symbols_needing_fetch),
    )

    # Three-stage fundamentals: yfinance fast → yfinance slow retry → EDGAR.
    # A "useful" result has at least one growth metric (eps OR revenue). Symbols
    # that return only trailing_eps without growth are treated as failures.
    def _is_useful(d: dict) -> bool:
        return d.get("net_income_growth") is not None or d.get("revenue_growth") is not None

    # Total ticks across all 3 sub-stages — we tick once per attempt-completion.
    # Conservative upper bound: 3 × needing_fetch (one tick per stage worst case).
    total_ticks = max(1, len(symbols_needing_fetch) * 3 if symbols_needing_fetch else 1)
    if p: p.begin_stage("fundamentals", total=total_ticks)

    fetched: dict[str, dict] = {}
    if symbols_needing_fetch:
        # ── 5a: First-pass yfinance (fast) ────────────────────────────────────
        log.info("Fundamentals pass 1/3: yfinance fast (%d symbols)", len(symbols_needing_fetch))
        pass1 = await _fetch_yf_fundamentals_batch(
            symbols_needing_fetch,
            on_done=(p.tick if p else None),
        )
        for sym, data in pass1.items():
            if _is_useful(data):
                fetched[sym] = data

        # ── 5b: Slow retry on yfinance for symbols missing growth metrics ──────
        retry_syms = [s for s in symbols_needing_fetch if s not in fetched]
        if retry_syms:
            log.info("Fundamentals pass 2/3: yfinance slow retry (%d symbols)", len(retry_syms))
            pass2 = await _fetch_yf_fundamentals_batch_slow(
                retry_syms,
                on_done=(p.tick if p else None),
            )
            for sym, data in pass2.items():
                if _is_useful(data):
                    fetched[sym] = data

        # ── 5c: EDGAR fallback for symbols with a known CIK ───────────────────
        still_missing = [s for s in symbols_needing_fetch if s not in fetched]
        if still_missing:
            edgar_syms = []
            for sym in still_missing:
                row = sym_row_map.get(sym)
                cik = (row.notes or "").removeprefix("cik:").strip() if row and (row.notes or "").startswith("cik:") else None
                if cik and len(cik) >= 6:  # valid CIK format
                    edgar_syms.append((sym, cik))
            log.info(
                "Fundamentals pass 3/3: EDGAR fallback (%d of %d have CIKs)",
                len(edgar_syms), len(still_missing),
            )
            if edgar_syms:
                edgar_map = await _fetch_edgar_fundamentals(edgar_syms, on_done=(p.tick if p else None))
                for sym, data in edgar_map.items():
                    if _is_useful(data):
                        fetched[sym] = data
            # Tick for symbols we couldn't try EDGAR on (no CIK)
            if p:
                no_cik = len(still_missing) - len(edgar_syms)
                for _ in range(no_cik):
                    p.tick()
    else:
        if p: p.set_processed(1)

    fundamental_map = {**fresh_fund, **fetched}
    stats.with_fundamentals = sum(1 for v in fundamental_map.values() if v)

    # ── Step 6: Upsert freshly-scored results ────────────────────────────────
    # Cached symbols are already in the DB with correct scores — only fresh
    # symbols get written. Both sets are combined for ranking below.
    if p: p.begin_stage("persist", total=len(scored))

    result_rows: list[ScreenerScore] = []
    for sym, df, tt, vcp in scored:
        sym_row = sym_row_map.get(sym)

        score_row = existing_scores.get(sym)
        if score_row is None:
            score_row = ScreenerScore(symbol=sym)
            session.add(score_row)
            existing_scores[sym] = score_row  # guard against mid-session duplicates

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

        # ── Pattern + buyability detection ───────────────────────────────────
        pat = detect_pattern(
            df,
            ma_50=float(tt.ma_50)  if tt.ma_50  else None,
            ma_200=float(tt.ma_200) if tt.ma_200 else None,
        )
        score_row.pattern_type    = pat.pattern_type if pat.pattern_type != "none" else None
        score_row.pattern_quality = Decimal(str(round(pat.quality, 3)))
        score_row.buyability      = pat.buyability
        score_row.pivot_price     = Decimal(str(round(pat.pivot_price, 4)))    if pat.pivot_price    is not None else None
        score_row.base_low        = Decimal(str(round(pat.base_low, 4)))       if pat.base_low       is not None else None
        score_row.base_length_days = pat.base_length_days
        score_row.base_depth_pct  = Decimal(str(round(pat.base_depth_pct, 2))) if pat.base_depth_pct is not None else None
        score_row.extension_pct   = Decimal(str(round(pat.extension_pct, 2)))  if pat.extension_pct  is not None else None

        fund = fundamental_map.get(sym)
        if fund:
            rev_g         = fund.get("revenue_growth")
            ni_g          = fund.get("net_income_growth")      # quarterly YoY
            annual_g      = fund.get("earnings_annual_growth")  # TTM/annual YoY
            margin        = fund.get("net_margin")
            roe           = fund.get("roe")
            eps           = fund.get("trailing_eps")
            sector        = fund.get("sector")

            # 5-point fundamental score (Minervini/CANSLIM):
            #   +1  EPS quarterly growth ≥ 25%  (CANSLIM "C")
            #   +1  Revenue growth ≥ 15%         (CANSLIM "S")
            #   +1  Net margin ≥ 10%             (quality / SEPA)
            #   +1  ROE ≥ 17%                    (Minervini quality filter)
            #   +1  Earnings acceleration: quarterly growth > annual growth
            #       (the most recent quarter accelerating vs trend — CANSLIM "A")
            score_pts = 0
            if ni_g   is not None and ni_g   >= 0.25: score_pts += 1
            if rev_g  is not None and rev_g  >= 0.15: score_pts += 1
            if margin is not None and margin >= 0.10: score_pts += 1
            if roe    is not None and roe    >= 0.17: score_pts += 1
            # Acceleration: quarterly EPS growth meaningfully above annual — sign of momentum
            if (ni_g is not None and annual_g is not None
                    and ni_g > annual_g + 0.05       # at least 5pp better than annual
                    and ni_g > 0.10):                # must still be genuinely positive
                score_pts += 1

            # Clamp growth rates to ±1000.0 (100,000%) before storing.
            # yfinance occasionally returns astronomical values for micro-caps
            # emerging from near-zero bases (e.g., rev went from $1k to $4M).
            # These values are meaningless for screening; cap them so DB columns
            # never overflow even if Numeric precision is widened further later.
            def _clamp(v: float | None, lo: float = -1000.0, hi: float = 1000.0) -> float | None:
                return max(lo, min(hi, v)) if v is not None else None

            score_row.fundamental_score      = Decimal(str(round(score_pts / 5.0, 3)))
            score_row.revenue_growth         = Decimal(str(round(_clamp(rev_g),    4))) if rev_g    is not None else None
            score_row.net_income_growth      = Decimal(str(round(_clamp(ni_g),     4))) if ni_g     is not None else None
            score_row.earnings_annual_growth = Decimal(str(round(_clamp(annual_g), 4))) if annual_g is not None else None
            score_row.net_margin             = Decimal(str(round(_clamp(margin, -10.0, 10.0), 4))) if margin is not None else None
            score_row.roe                    = Decimal(str(round(_clamp(roe,    -100.0, 100.0), 4))) if roe   is not None else None
            score_row.eps_ttm                = Decimal(str(round(eps,       4))) if eps      is not None else None
            score_row.sector                 = sector or score_row.sector
            score_row.fundamental_error      = None
        else:
            score_row.fundamental_score = Decimal(0)

        result_rows.append(score_row)
        if p: p.tick()

    await session.flush()

    # Merge cached rows (scored earlier today) into result_rows for ranking.
    # Their DB values are already correct — we don't touch their scored_at.
    all_ranked_rows = result_rows + cached_rows
    log.info(
        "Ranking %d rows (%d fresh + %d cached from today)",
        len(all_ranked_rows), len(result_rows), len(cached_rows),
    )

    # ── Step 7: RS rank + EPS/SMR rank + composite ──────────────────────────
    if p: p.begin_stage("rank", total=len(all_ranked_rows))
    rs_vals = [(s, float(s.rs_raw)) for s in all_ranked_rows if s.rs_raw is not None]
    rs_vals.sort(key=lambda x: x[1])
    n = len(rs_vals)
    for rank, (s, _) in enumerate(rs_vals):
        s.rs_rank = int((rank / max(n - 1, 1)) * 99) if n > 1 else 50

    # ── Step 8: EPS rank + SMR rank (IBD-style 0-99 percentile) ──────────────
    # EPS rank: weighted blend of quarterly EPS YoY growth + TTM EPS positivity.
    # Quarterly growth dominates (Minervini's #1 metric); TTM EPS adds a small
    # positive-earnings bonus. Symbols missing all data get rank=None (sorts low).
    def _eps_raw(s: ScreenerScore) -> float | None:
        ni_g = float(s.net_income_growth) if s.net_income_growth is not None else None
        eps  = float(s.eps_ttm)           if s.eps_ttm           is not None else None
        if ni_g is None and eps is None:
            return None
        # Cap insane values from yfinance (e.g. small base inflating growth)
        eps_growth_capped = min(max(ni_g if ni_g is not None else 0.0, -1.0), 5.0)
        eps_positive      = 1.0 if (eps is not None and eps > 0) else 0.0
        # Weight: 80% growth, 20% positivity
        return 0.80 * eps_growth_capped + 0.20 * eps_positive

    # SMR rank: revenue growth, net margin, ROE — equal-weighted z-score blend
    def _smr_raw(s: ScreenerScore) -> float | None:
        rev_g  = float(s.revenue_growth) if s.revenue_growth is not None else None
        margin = float(s.net_margin)     if s.net_margin     is not None else None
        roe    = float(s.roe)            if s.roe            is not None else None
        if rev_g is None and margin is None and roe is None:
            return None
        rev_capped    = min(max(rev_g  if rev_g  is not None else 0.0, -1.0), 3.0)
        margin_capped = min(max(margin if margin is not None else 0.0, -1.0), 1.0)
        roe_capped    = min(max(roe    if roe    is not None else 0.0, -1.0), 2.0)
        return rev_capped + margin_capped + roe_capped

    def _assign_percentile(rows, raw_fn, attr):
        scored = [(r, raw_fn(r)) for r in rows]
        with_data = [(r, v) for r, v in scored if v is not None]
        with_data.sort(key=lambda x: x[1])
        m = len(with_data)
        for rank, (r, _) in enumerate(with_data):
            setattr(r, attr, int((rank / max(m - 1, 1)) * 99) if m > 1 else 50)
        for r, v in scored:
            if v is None:
                setattr(r, attr, None)

    _assign_percentile(all_ranked_rows, _eps_raw, "eps_rank")
    _assign_percentile(all_ranked_rows, _smr_raw, "smr_rank")

    # ── Step 9: Composite score ───────────────────────────────────────────────
    # Components: TT 12% + VCP 15% + RS 20% + EPS 20% + SMR 13% + Pattern 20%.
    # Extended/broken/frozen stocks → composite = 0.
    # Pattern quality is multiplied by an EV factor that reflects literature
    # win-rate × avg-gain so HTF/Asc-Triangle setups naturally outrank CWH/Flat.
    PATTERN_EV_MULT = {
        "high_tight_flag":    2.00,  # ~6.9R expectancy — rare, take always
        "ascending_triangle": 1.40,  # ~3.6R
        "vcp":                1.00,
        "cwh":                1.00,
        "three_weeks_tight":  1.00,
        "bull_flag":          0.85,  # ~1.8R
        "flat_base":          0.75,  # ~1.4R
        None:                 0.50,
    }

    for s in all_ranked_rows:
        # Strict exclusion — extended/broken/frozen stocks aren't buyable.
        if s.buyability in ("extended", "broken", "frozen"):
            s.composite_score = Decimal(0)
            continue

        tt_norm      = s.tt_score / 8.0
        vcp_norm     = float(s.vcp_score)
        rs_norm      = (s.rs_rank or 50) / 99.0
        eps_norm     = (s.eps_rank if s.eps_rank is not None else 0) / 99.0
        smr_norm     = (s.smr_rank if s.smr_rank is not None else 0) / 99.0
        pattern_q    = float(s.pattern_quality) if s.pattern_quality is not None else 0.0
        ev_mult      = PATTERN_EV_MULT.get(s.pattern_type, 0.50)
        pattern_norm = min(1.0, pattern_q * ev_mult)   # cap at 1.0 to keep composite ≤ 1

        # Buyability multiplier — strongly reward at_pivot, penalize no-pattern
        if s.buyability == "at_pivot":
            buyability_mult = 1.15
        elif s.buyability == "in_base":
            buyability_mult = 1.00
        else:
            buyability_mult = 0.80   # no_pattern stocks are background candidates

        composite = (
            tt_norm      * 0.12 +
            vcp_norm     * 0.15 +
            rs_norm      * 0.20 +
            eps_norm     * 0.20 +
            smr_norm     * 0.13 +
            pattern_norm * 0.20
        ) * buyability_mult

        s.composite_score = Decimal(str(round(min(composite, 1.0), 3)))

    if p: p.set_processed(len(all_ranked_rows))
    await session.commit()
    all_ranked_rows.sort(key=lambda s: float(s.composite_score), reverse=True)

    if p:
        p.finish(stats={
            "universe_size": stats.universe_size,
            "eod_downloaded_bars": stats.eod_downloaded,
            "tt_passing": stats.tt_passing,
            "scored": stats.scored,
            "with_fundamentals": stats.with_fundamentals,
        })
    return all_ranked_rows, stats


async def get_screener_results(
    session: AsyncSession,
    *,
    min_tt: int = 0,
    min_vcp: float = 0.0,
    min_eps: int = 0,
    min_rs: int = 0,
    min_composite: int = 0,
    buyability: str | None = None,    # comma-separated buyability values
    pattern: str | None = None,       # comma-separated pattern values
    sector: str | None = None,
    max_age_days: int = 5,
) -> list[ScreenerScore]:
    q = select(ScreenerScore).order_by(ScreenerScore.composite_score.desc())
    # Freshness cutoff: symbols that dropped out of recent scans keep their old
    # rows (with stale pivots/extensions computed from old closes) — never show
    # them. 5 days spans weekends + holidays between nightly scans.
    if max_age_days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        q = q.where(ScreenerScore.scored_at >= cutoff)
    result = await session.execute(q)
    rows = result.scalars().all()

    # Apply filters in Python (simple enough, avoids dynamic SQLAlchemy)
    if min_tt:
        rows = [r for r in rows if r.tt_score >= min_tt]
    if min_vcp:
        rows = [r for r in rows if float(r.vcp_score) >= min_vcp]
    if min_eps:
        rows = [r for r in rows if (r.eps_rank or 0) >= min_eps]
    if min_rs:
        rows = [r for r in rows if (r.rs_rank or 0) >= min_rs]
    if min_composite:
        rows = [r for r in rows if float(r.composite_score) * 100 >= min_composite]
    if buyability:
        wanted = {v.strip().lower() for v in buyability.split(",") if v.strip()}
        rows = [r for r in rows if (r.buyability or "").lower() in wanted]
    if pattern:
        wanted_p = {v.strip().lower() for v in pattern.split(",") if v.strip()}
        rows = [r for r in rows if (r.pattern_type or "").lower() in wanted_p]
    if sector:
        rows = [r for r in rows if (r.sector or "").lower() == sector.lower()]

    return rows
