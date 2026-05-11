"""Stock universe builder — pulls from free public sources.

Sources (in order of priority for deduplication):
  1. S&P 500       from Wikipedia (HTML table)
  2. NASDAQ 100    from Wikipedia
  3. TSX 60        from Wikipedia (Canadian)
  4. Manual watchlist symbols (always included)

All sources are merged and deduplicated. The universe is persisted in the
screener_symbols table with is_active=True and source metadata.

The SEC universe from data.sec.gov/files/company_tickers.json is used
as an enrichment step to attach CIK numbers (needed for EDGAR fundamentals)
to symbols that match.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScreenerSymbol

log = logging.getLogger(__name__)

SEC_TICKERS_URL          = "https://www.sec.gov/files/company_tickers.json"
SEC_EXCHANGE_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

# Exchanges we treat as real US markets (exclude OTC / Pink Sheets)
_VALID_US_EXCHANGES = {"Nasdaq", "NYSE", "CBOE", "NYSEArca", "NYSE Arca", "NASDAQ"}


def _sec_user_agent() -> str:
    from app.config import get_settings
    return get_settings().edgar_user_agent

# Wikipedia table indices that hold ticker symbols
_WIKI_TABLES = {
    "sp500":    ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",    0, "Symbol"),
    "sp400":    ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",    0, "Symbol"),
    "sp600":    ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",    0, "Symbol"),
    "nasdaq100":("https://en.wikipedia.org/wiki/Nasdaq-100",                      5, "Ticker"),
    "tsx60":    ("https://en.wikipedia.org/wiki/S%26P/TSX_60",                    1, "Symbol"),
}
# Note: S&P 600 is the SmallCap 600 — the highest-quality small-cap index.
# Minervini's biggest winners were typically stocks in this market-cap range
# ($300M–$3B) that had not yet been fully discovered by institutions.

# TSX-listed symbols need the .TO suffix for yfinance.
# We add it here so price downloads and info fetches work correctly.
_TSX_SOURCES = {"tsx60"}


_WIKI_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _fetch_wikipedia_tickers(source_key: str) -> list[dict]:
    import io
    url, table_idx, col = _WIKI_TABLES[source_key]
    try:
        # Wikipedia blocks urllib's default UA — use httpx with a browser UA.
        with httpx.Client(timeout=20, headers={"User-Agent": _WIKI_UA}, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            html = r.text

        tables = pd.read_html(io.StringIO(html))
        is_tsx = source_key in _TSX_SOURCES

        def _extract(df) -> list[str]:
            for candidate in [col, "Symbol", "Ticker", "Symbols"]:
                if candidate in df.columns:
                    raw = df[candidate].dropna().astype(str).str.strip().str.upper().tolist()
                    syms = [s.replace(".", "-") for s in raw if s and s != "NAN"]
                    if is_tsx:
                        # Add .TO suffix for TSX-listed symbols so yfinance can find them.
                        # BIP-UN → BIP-UN.TO, ATD → ATD.TO etc.
                        syms = [s if s.endswith(".TO") else f"{s}.TO" for s in syms]
                    return syms
            return []

        if table_idx >= len(tables):
            log.warning("Table index %d out of range for %s (%d tables found)", table_idx, source_key, len(tables))
            for i, df in enumerate(tables):
                syms = _extract(df)
                if syms:
                    log.info("Found tickers in table %d for %s", i, source_key)
                    return [{"symbol": s, "source": source_key} for s in syms]
            return []

        syms = _extract(tables[table_idx])
        if syms:
            return [{"symbol": s, "source": source_key} for s in syms]
        log.warning("Could not find ticker column in %s table %d", url, table_idx)
        return []
    except Exception:
        log.exception("Failed to fetch %s universe from Wikipedia", source_key)
        return []


# Ticker suffixes that identify non-common-stock securities listed on the
# same exchanges as equities. We skip these so they don't bloat the download
# queue with symbols that yfinance has no useful price/fundamental data for.
#
# Examples:
#   ACMR-W   (warrant)
#   AJAX-U   (unit)
#   PSTH-WT  (warrant)
#   LFTR-R   (right)
#   FRT-PC   (preferred series C)
#   BAC-PE   (preferred series E)
#
# Class-shares like BRK-A, BRK-B are intentionally kept (one-letter A-E that
# are NOT prefixed with P are kept).
_NON_EQUITY_SUFFIXES: frozenset[str] = frozenset({
    "W",   "WS",  "WT",  "WW",         # warrants
    "R",   "RT",  "RIGT",              # rights / subscription receipts
    "U",   "UN",  "UNIT",              # units (often SPAC units)
    "Z",                                # misc (often temporary symbols)
    "P", "PA", "PB", "PC", "PD",       # preferred shares (multiple series)
    "PE", "PF", "PG", "PH", "PI",
    "PJ", "PK", "PL", "PM", "PN",
    "PO", "PP", "PQ", "PR",
})


def _is_common_stock_ticker(ticker: str) -> bool:
    """Return False for tickers that are almost certainly non-equity securities."""
    if "-" not in ticker:
        return True
    parts = ticker.rsplit("-", 1)
    suffix = parts[-1].upper()
    return suffix not in _NON_EQUITY_SUFFIXES


async def _fetch_sec_exchange_universe() -> list[dict]:
    """Fetch NYSE/Nasdaq/CBOE listed stocks from SEC exchange JSON.

    Returns rows with symbol, cik, and source='sec_all'. Filters out obvious
    non-equity securities (warrants, rights, units) that inflate the universe
    and have no useful price or fundamental data in yfinance.

    Note: deduplication against the curated Wikipedia indices (S&P 500 etc.)
    happens in build_universe — symbols already added from those sources are
    not re-added from this list.
    """
    try:
        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": _sec_user_agent()}
        ) as client:
            r = await client.get(SEC_EXCHANGE_TICKERS_URL)
            r.raise_for_status()
            data = r.json()
    except Exception:
        log.warning("Could not fetch SEC exchange universe")
        return []

    fields = data.get("fields", [])
    rows: list[dict] = []
    skipped_non_equity = 0
    for item in data.get("data", []):
        d = dict(zip(fields, item))
        exchange = d.get("exchange") or ""
        if exchange not in _VALID_US_EXCHANGES:
            continue
        ticker = (d.get("ticker") or "").strip().upper().replace(".", "-")
        cik_num = d.get("cik")
        if not ticker or not cik_num:
            continue
        if not _is_common_stock_ticker(ticker):
            skipped_non_equity += 1
            continue
        rows.append({
            "symbol": ticker,
            "cik": str(cik_num).zfill(10),
            "source": "sec_all",
        })

    log.info(
        "SEC exchange universe: %d equity tickers (%d non-equity skipped)",
        len(rows), skipped_non_equity,
    )
    return rows


async def _fetch_sec_cik_map() -> dict[str, str]:
    """Return {ticker_upper: cik} for all SEC-registered companies (fallback)."""
    try:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": _sec_user_agent()}) as client:
            r = await client.get(SEC_TICKERS_URL)
            r.raise_for_status()
            data = r.json()
    except Exception:
        log.warning("Could not fetch SEC universe (CIK map) — fundamentals will be skipped")
        return {}

    result: dict[str, str] = {}
    for item in (data.values() if isinstance(data, dict) else data):
        ticker = (item.get("ticker") or "").strip().upper()
        cik_num = item.get("cik_str")
        if ticker and cik_num:
            result[ticker] = str(cik_num).zfill(10)
    return result


async def build_universe(session: AsyncSession) -> dict[str, int]:
    """Fetch all configured sources, merge, upsert into screener_symbols.

    Sources (priority order for dedup):
      1. Wikipedia curated indices (S&P 500/400/600, NASDAQ 100, TSX 60)
         — high-quality, index-member stocks with known sector/weight
      2. SEC exchange JSON (all NYSE/Nasdaq/CBOE listed stocks, ~7,500)
         — catches everything outside the indices: ETFs, small-caps, etc.
         CIKs pre-attached so EDGAR fundamentals fetch is instant.

    Returns {source: count}.
    """
    import asyncio
    loop = asyncio.get_event_loop()

    # 1. Fetch Wikipedia curated indices in parallel
    wiki_results = await asyncio.gather(*[
        loop.run_in_executor(None, _fetch_wikipedia_tickers, source_key)
        for source_key in _WIKI_TABLES
    ])

    # 2. Fetch full SEC exchange universe (all US stocks with CIK)
    sec_rows = await _fetch_sec_exchange_universe()

    # Merge: curated indices first (they win dedup), then SEC for broader coverage
    seen: dict[str, str] = {}
    all_rows: list[dict] = []
    per_source: dict[str, int] = {}

    # Wikipedia rows go first — they override if symbol also in SEC
    for source_key, rows in zip(_WIKI_TABLES.keys(), wiki_results):
        per_source[source_key] = len(rows)
        for row in rows:
            sym = row["symbol"]
            if sym not in seen:
                seen[sym] = source_key
                all_rows.append(row)

    # SEC rows fill in everything not already covered
    per_source["sec_all"] = 0
    for row in sec_rows:
        sym = row["symbol"]
        if sym not in seen:
            seen[sym] = "sec_all"
            all_rows.append(row)
            per_source["sec_all"] += 1

    log.info("Universe: %s → %d unique symbols total", per_source, len(all_rows))

    # CIK map for symbols that came from Wikipedia (sec_all already has CIKs)
    cik_map = await _fetch_sec_cik_map()

    # 3. Upsert into screener_symbols
    # Load existing rows
    existing_result = await session.execute(select(ScreenerSymbol))
    existing = {s.symbol: s for s in existing_result.scalars().all()}

    counts: dict[str, int] = {}
    for row in all_rows:
        sym = row["symbol"]
        source = row["source"]
        # SEC exchange rows carry CIK directly; Wikipedia rows use the cik_map.
        cik = row.get("cik")
        if not cik:
            bare_sym = sym.removesuffix(".TO").removesuffix(".V")
            cik = cik_map.get(sym) or cik_map.get(bare_sym)
        counts[source] = counts.get(source, 0) + 1

        if sym in existing:
            s = existing[sym]
            s.is_active = True
            if cik and not s.notes:
                s.notes = f"cik:{cik}"
        else:
            note = f"cik:{cik}" if cik else None
            s = ScreenerSymbol(symbol=sym, notes=note)
            session.add(s)
            existing[sym] = s

    # Never deactivate — the full SEC list is the ground truth now
    # (all rows come from authoritative sources)

    await session.commit()
    return counts


def extract_cik(symbol_row: ScreenerSymbol) -> str | None:
    """Extract CIK from notes field (format 'cik:XXXXXXXXXX')."""
    notes = symbol_row.notes or ""
    if notes.startswith("cik:"):
        return notes.split(":", 1)[1].strip() or None
    return None
