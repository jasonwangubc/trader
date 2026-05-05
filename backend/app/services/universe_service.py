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

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "trader-screener/1.0 contact@example.com"

# Wikipedia table indices that hold ticker symbols
_WIKI_TABLES = {
    "sp500":    ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",    0, "Symbol"),
    "nasdaq100":("https://en.wikipedia.org/wiki/Nasdaq-100",                      5, "Ticker"),
    "tsx60":    ("https://en.wikipedia.org/wiki/S%26P/TSX_60",                    1, "Symbol"),
}


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
        if table_idx >= len(tables):
            log.warning("Table index %d out of range for %s (%d tables found)", table_idx, source_key, len(tables))
            # Try all tables to find the right one
            for i, df in enumerate(tables):
                for candidate in [col, "Symbol", "Ticker", "Symbols"]:
                    if candidate in df.columns:
                        syms = df[candidate].dropna().astype(str).str.strip().str.upper().tolist()
                        syms = [s.replace(".", "-") for s in syms if s and s != "NAN"]
                        if syms:
                            log.info("Found tickers in table %d column '%s' for %s", i, candidate, source_key)
                            return [{"symbol": s, "source": source_key} for s in syms]
            return []

        df = tables[table_idx]
        for candidate in [col, "Symbol", "Ticker", "Symbols"]:
            if candidate in df.columns:
                syms = df[candidate].dropna().astype(str).str.strip().str.upper().tolist()
                syms = [s.replace(".", "-") for s in syms if s and s != "NAN"]
                return [{"symbol": s, "source": source_key} for s in syms]
        log.warning("Could not find ticker column in %s table %d — available: %s", url, table_idx, list(df.columns)[:10])
        return []
    except Exception:
        log.exception("Failed to fetch %s universe from Wikipedia", source_key)
        return []


async def _fetch_sec_cik_map() -> dict[str, str]:
    """Return {ticker_upper: cik} for all SEC-registered companies."""
    try:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": SEC_USER_AGENT}) as client:
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
    """Fetch all sources, merge, enrich with CIK, upsert into screener_symbols.
    Returns {source: count}.
    """
    # 1. Pull ticker lists from Wikipedia (run sync in thread to avoid blocking)
    import asyncio
    loop = asyncio.get_event_loop()

    sp500 = await loop.run_in_executor(None, _fetch_wikipedia_tickers, "sp500")
    nasdaq = await loop.run_in_executor(None, _fetch_wikipedia_tickers, "nasdaq100")
    tsx60  = await loop.run_in_executor(None, _fetch_wikipedia_tickers, "tsx60")

    # Merge — first-seen source wins for deduplication
    seen: dict[str, str] = {}
    all_rows: list[dict] = []
    for row in sp500 + nasdaq + tsx60:
        sym = row["symbol"]
        if sym not in seen:
            seen[sym] = row["source"]
            all_rows.append(row)

    log.info("Universe: %d from S&P 500, %d from NASDAQ 100, %d from TSX 60 → %d unique",
             len(sp500), len(nasdaq), len(tsx60), len(all_rows))

    # 2. CIK enrichment from SEC
    cik_map = await _fetch_sec_cik_map()

    # 3. Upsert into screener_symbols
    # Load existing rows
    existing_result = await session.execute(select(ScreenerSymbol))
    existing = {s.symbol: s for s in existing_result.scalars().all()}

    counts: dict[str, int] = {}
    for row in all_rows:
        sym = row["symbol"]
        source = row["source"]
        cik = cik_map.get(sym)
        counts[source] = counts.get(source, 0) + 1

        if sym in existing:
            s = existing[sym]
            s.is_active = True
            # Update CIK if we found it and didn't have it
            if cik and not s.notes:
                s.notes = f"cik:{cik}"
        else:
            note = f"cik:{cik}" if cik else None
            s = ScreenerSymbol(symbol=sym, notes=note)
            session.add(s)
            existing[sym] = s

    # Keep existing manually-added symbols active (don't deactivate them)
    # Only deactivate auto-discovered symbols that are no longer in any index
    auto_sources = {"sp500", "nasdaq100", "tsx60"}
    auto_syms = {r["symbol"] for r in all_rows}
    for sym, s in existing.items():
        if sym not in auto_syms and s.notes and any(src in (s.notes or "") for src in auto_sources):
            s.is_active = False

    await session.commit()
    return counts


def extract_cik(symbol_row: ScreenerSymbol) -> str | None:
    """Extract CIK from notes field (format 'cik:XXXXXXXXXX')."""
    notes = symbol_row.notes or ""
    if notes.startswith("cik:"):
        return notes.split(":", 1)[1].strip() or None
    return None
