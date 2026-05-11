import Link from "next/link";
import { RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { type ResultsPage } from "@/lib/screener";
import { ResultsTable } from "./results-table";
import { DataStatusPanel } from "./data-status-panel";
import { AddSymbolButton } from "./add-symbol-button";
import { ScanProgress } from "./scan-progress";

export const metadata = { title: "Screener" };

// ---- Types ----

interface PriceCoverage {
  symbols_total: number;
  symbols_with_recent_bars: number;
  pct_covered: number;
  latest_bar_date: string | null;
  is_stale: boolean;
  missing_symbols: string[];
}

interface FundamentalCoverage {
  symbols_scored: number;
  symbols_with_fundamentals: number;
  pct_covered: number;
  note: string;
  top_missing: Array<{ symbol: string; tt_score: number; vcp_score: number; rs_rank: number | null }>;
}

interface ScoreCoverage {
  total_scored: number;
  last_run_at: string | null;
  tt_distribution: Record<string, number>;
}

export interface ScreenerHealth {
  universe_total: number;
  price: PriceCoverage;
  fundamentals: FundamentalCoverage;
  scores: ScoreCoverage;
}

interface ScanProgressData {
  stage: string;
  stage_label: string;
  stage_index: number;
  total_stages: number;
  processed: number;
  total: number;
  pct: number;
  started_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  error: string | null;
}

interface SyncStatus {
  running: boolean;
  message: string;
  progress?: ScanProgressData | null;
}

// Quick-filter chip definitions (preset combos that match common screening intents)
interface ChipDef {
  label: string;
  hint: string;
  params: Record<string, string>;
}

const CHIPS: ChipDef[] = [
  { label: "All",         hint: "No filters",                                            params: {} },
  { label: "Setting up",  hint: "At pivot or in base — actionable now (Recommended)",   params: { buyability: "at_pivot,in_base" } },
  { label: "At pivot",    hint: "Within ±5% of pivot — buyable today",                  params: { buyability: "at_pivot" } },
  { label: "VCP",         hint: "Volatility Contraction Pattern setups only",           params: { pattern: "vcp", buyability: "at_pivot,in_base" } },
  { label: "Cup & Handle",hint: "Cup with Handle setups only",                          params: { pattern: "cwh", buyability: "at_pivot,in_base" } },
  { label: "Flat Base",   hint: "Flat base setups only",                                params: { pattern: "flat_base", buyability: "at_pivot,in_base" } },
  { label: "Leaders",     hint: "Composite ≥ 70 (includes extended)",                   params: { min_composite: "70" } },
  { label: "EPS ≥ 80",    hint: "Top quintile of earnings strength",                    params: { min_eps: "80" } },
  { label: "RS ≥ 80",     hint: "Top quintile of relative strength",                    params: { min_rs: "80" } },
  { label: "CANSLIM",     hint: "EPS+RS top quartile + clean setup",                    params: { min_eps: "75", min_rs: "75", buyability: "at_pivot,in_base" } },
];

const PAGE_SIZE = 50;

// ---- Page ----

export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<{
    min_tt?: string;
    min_vcp?: string;
    min_eps?: string;
    min_rs?: string;
    min_composite?: string;
    buyability?: string;
    pattern?: string;
    page?: string;
  }>;
}) {
  const params = await searchParams;
  const minTT        = parseInt(params.min_tt        ?? "0") || 0;
  const minVCP       = parseFloat(params.min_vcp     ?? "0") || 0;
  const minEPS       = parseInt(params.min_eps       ?? "0") || 0;
  const minRS        = parseInt(params.min_rs        ?? "0") || 0;
  const minComposite = parseInt(params.min_composite ?? "0") || 0;
  const buyabilityParam = params.buyability ?? "";
  const patternParam    = params.pattern ?? "";
  const page         = Math.max(1, parseInt(params.page ?? "1") || 1);

  const qp = new URLSearchParams();
  if (minTT)        qp.set("min_tt",        String(minTT));
  if (minVCP)       qp.set("min_vcp",       String(minVCP));
  if (minEPS)       qp.set("min_eps",       String(minEPS));
  if (minRS)        qp.set("min_rs",        String(minRS));
  if (minComposite) qp.set("min_composite", String(minComposite));
  if (buyabilityParam) qp.set("buyability", buyabilityParam);
  if (patternParam)    qp.set("pattern",    patternParam);
  qp.set("page",      String(page));
  qp.set("page_size", String(PAGE_SIZE));

  let resultsPage: ResultsPage | null = null;
  let health: ScreenerHealth | null = null;
  let syncStatus: SyncStatus = { running: false, message: "" };
  let error: string | null = null;

  try {
    [resultsPage, health, syncStatus] = await Promise.all([
      api<ResultsPage>(`/api/screener/results?${qp}`),
      api<ScreenerHealth>("/api/screener/health"),
      api<SyncStatus>("/api/screener/sync/status"),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  const results = resultsPage?.items ?? [];
  const lastRun = health?.scores.last_run_at ? new Date(health.scores.last_run_at) : null;
  const activeFilters = { minTT, minVCP, minEPS, minRS, minComposite, buyability: buyabilityParam, pattern: patternParam };
  const anyFilter = minTT || minVCP || minEPS || minRS || minComposite || buyabilityParam || patternParam;

  return (
    <main className="container mx-auto max-w-[88rem] p-6 sm:p-8">
      {/* Header — title + scan button + last-run timestamp */}
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Screener</h1>
          <p className="text-muted-foreground mt-0.5 text-xs">
            {health ? (
              <>
                {health.scores.total_scored.toLocaleString()} scored ·{" "}
                {lastRun ? `last ${timeAgo(lastRun)}` : "never run"}
                {health.price.is_stale && <span className="text-amber-500"> · price data stale</span>}
              </>
            ) : (
              "S&P 500 + 400 + 600 + NASDAQ 100 + TSX 60"
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <AddSymbolButton />
          <ScanButton running={syncStatus.running} />
        </div>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-5 rounded-md border p-4 text-sm">{error}</div>
      )}

      {/* Live scan progress (visible only while scanning, plus briefly after) */}
      <div className="mb-3">
        <ScanProgress
          initialRunning={syncStatus.running}
          initialProgress={syncStatus.progress ?? null}
        />
      </div>

      <div className="space-y-3 min-w-0">
        {/* Quick filter chips */}
        <FilterChips active={activeFilters} totalResults={resultsPage?.total ?? 0} />

        {/* Results table */}
        <ResultsTable results={results} />

        {/* Pagination */}
        {resultsPage && resultsPage.pages > 1 && (
          <Pagination
            page={page}
            pages={resultsPage.pages}
            total={resultsPage.total}
            activeFilters={activeFilters}
          />
        )}

        {/* Data status — collapsed by default */}
        {health && <DataStatusPanel health={health} />}
      </div>

      {!anyFilter && results.length === 0 && health && health.scores.total_scored === 0 && (
        <div className="mt-6 rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
          No data yet. Click <span className="font-medium text-foreground">Run scan</span> to download the universe and score all stocks.
        </div>
      )}
    </main>
  );
}

// ---- Quick filter chips ----

function FilterChips({
  active, totalResults,
}: {
  active: { minTT: number; minVCP: number; minEPS: number; minRS: number; minComposite: number; buyability: string; pattern: string };
  totalResults: number;
}) {
  const isActive = (chip: ChipDef): boolean => {
    const want = chip.params;
    const has = (k: string, v: number | string) => {
      const wv = want[k];
      if (wv === undefined) return v === 0 || v === "0" || v === "";
      return String(v) === wv;
    };
    return (
      has("min_tt", active.minTT) &&
      has("min_vcp", active.minVCP) &&
      has("min_eps", active.minEPS) &&
      has("min_rs", active.minRS) &&
      has("min_composite", active.minComposite) &&
      has("buyability", active.buyability) &&
      has("pattern", active.pattern)
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-muted-foreground text-xs font-medium pr-1">
        {totalResults.toLocaleString()} {totalResults === 1 ? "stock" : "stocks"}
      </span>
      <div className="flex flex-wrap gap-1.5">
        {CHIPS.map(chip => {
          const active_ = isActive(chip);
          const qs = new URLSearchParams(chip.params).toString();
          const href = qs ? `/screener?${qs}` : "/screener";
          return (
            <Link
              key={chip.label}
              href={href}
              title={chip.hint}
              className={`inline-flex h-7 items-center rounded-full border px-3 text-xs font-medium transition-colors ${
                active_
                  ? "border-primary/50 bg-primary/15 text-primary"
                  : "border-border/60 text-muted-foreground hover:border-border hover:text-foreground"
              }`}
            >
              {chip.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}

// ---- Pagination ----

function Pagination({
  page, pages, total, activeFilters,
}: {
  page: number; pages: number; total: number;
  activeFilters: { minTT: number; minVCP: number; minEPS: number; minRS: number; minComposite: number; buyability: string; pattern: string };
}) {
  const buildUrl = (p: number) => {
    const q = new URLSearchParams();
    if (activeFilters.minTT)        q.set("min_tt",        String(activeFilters.minTT));
    if (activeFilters.minVCP)       q.set("min_vcp",       String(activeFilters.minVCP));
    if (activeFilters.minEPS)       q.set("min_eps",       String(activeFilters.minEPS));
    if (activeFilters.minRS)        q.set("min_rs",        String(activeFilters.minRS));
    if (activeFilters.minComposite) q.set("min_composite", String(activeFilters.minComposite));
    if (activeFilters.buyability)   q.set("buyability",    activeFilters.buyability);
    if (activeFilters.pattern)      q.set("pattern",       activeFilters.pattern);
    if (p > 1) q.set("page", String(p));
    return `/screener${q.size ? "?" + q : ""}`;
  };

  return (
    <div className="flex items-center justify-between px-1 pt-1">
      <span className="text-muted-foreground text-xs">
        Page {page} of {pages} · {total.toLocaleString()} total
      </span>
      <div className="flex items-center gap-1">
        {page > 1 && (
          <Link href={buildUrl(page - 1)} className="border-border/60 hover:bg-muted inline-flex h-7 items-center rounded border px-2.5 text-xs">
            ← Prev
          </Link>
        )}
        {page < pages && (
          <Link href={buildUrl(page + 1)} className="border-border/60 hover:bg-muted inline-flex h-7 items-center rounded border px-2.5 text-xs">
            Next →
          </Link>
        )}
      </div>
    </div>
  );
}

// ---- Scan button ----

function ScanButton({ running }: { running: boolean }) {
  return (
    <div className="flex items-center gap-2">
      {running && (
        <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          Scanning…
        </span>
      )}
      <Link
        href="/screener/scan"
        className={`inline-flex h-9 items-center gap-2 rounded-md px-4 text-sm font-medium transition-colors ${
          running
            ? "border-input bg-muted pointer-events-none border opacity-60"
            : "bg-primary text-primary-foreground hover:bg-primary/90"
        }`}
      >
        <RefreshCw className="h-3.5 w-3.5" />
        {running ? "Scan running…" : "Run scan"}
      </Link>
    </div>
  );
}

// ---- Helpers ----

function timeAgo(d: Date): string {
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)} days ago`;
}
