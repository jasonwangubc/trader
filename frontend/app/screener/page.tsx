import Link from "next/link";
import { CheckCircle, AlertTriangle, RefreshCw, Clock, BarChart2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import {
  type ResultsPage,
  type ScoreResult,
  type ScreenerSymbol,
  TT_CRITERIA_LABELS,
  fmtPct,
} from "@/lib/screener";
import { StockChart } from "@/components/stock-chart";
import { WatchlistManager } from "./watchlist-manager";

// ---- Types ----

interface PriceCoverage {
  symbols_total: number;
  symbols_with_recent_bars: number;
  pct_covered: number;
  latest_bar_date: string | null;
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

interface ScreenerHealth {
  universe_total: number;
  price: PriceCoverage;
  fundamentals: FundamentalCoverage;
  scores: ScoreCoverage;
}

interface SyncStatus { running: boolean; message: string }

// ---- Page ----

const PAGE_SIZE = 20;

export default async function ScreenerPage({
  searchParams,
}: {
  searchParams: Promise<{ min_tt?: string; min_vcp?: string; page?: string }>;
}) {
  const params = await searchParams;
  const minTT  = parseInt(params.min_tt  ?? "0") || 0;
  const minVCP = parseFloat(params.min_vcp ?? "0") || 0;
  const page   = Math.max(1, parseInt(params.page ?? "1") || 1);

  let resultsPage: ResultsPage | null = null;
  let watchlist: ScreenerSymbol[] = [];
  let health: ScreenerHealth | null = null;
  let syncStatus: SyncStatus = { running: false, message: "" };
  let error: string | null = null;

  const qp = new URLSearchParams();
  if (minTT)  qp.set("min_tt",   String(minTT));
  if (minVCP) qp.set("min_vcp",  String(minVCP));
  qp.set("page",      String(page));
  qp.set("page_size", String(PAGE_SIZE));
  const resultPath = `/api/screener/results?${qp}`;

  try {
    [resultsPage, watchlist, health, syncStatus] = await Promise.all([
      api<ResultsPage>(resultPath),
      api<ScreenerSymbol[]>("/api/screener/watchlist"),
      api<ScreenerHealth>("/api/screener/health"),
      api<SyncStatus>("/api/screener/sync/status"),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  const results = resultsPage?.items ?? [];

  return (
    <main className="container mx-auto max-w-7xl p-6 sm:p-10">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Screener</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            S&P 500 · NASDAQ 100 · TSX 60 — scored daily on technical + fundamental criteria
          </p>
        </div>
        <ScanButton running={syncStatus.running} />
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">{error}</div>
      )}

      {/* Data health */}
      {health ? (
        <HealthDashboard health={health} syncRunning={syncStatus.running} />
      ) : (
        <div className="mb-6 rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
          No data yet. Click "Run scan" to download the universe and score all stocks.
        </div>
      )}

      {/* Results */}
      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_18rem]">
        <div className="space-y-4">
          <FilterBar
            currentMinTT={minTT}
            currentMinVCP={minVCP}
            totalResults={resultsPage?.total ?? 0}
            page={page}
            pages={resultsPage?.pages ?? 1}
          />
          {results.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center text-sm text-muted-foreground">
                {health && health.scores.total_scored === 0
                  ? "No results yet — run a scan to score the full universe."
                  : "No stocks match the current filters. Try lowering the minimum scores."}
              </CardContent>
            </Card>
          ) : (
            results.map(r => <ResultCard key={r.symbol} result={r} />)
          )}
        </div>
        <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
          <WatchlistManager initialSymbols={watchlist} />
        </aside>
      </div>
    </main>
  );
}

// ---- Scan button (server-side link, triggers background task) ----

function ScanButton({ running }: { running: boolean }) {
  return (
    <div className="flex items-center gap-2">
      {running && (
        <span className="text-muted-foreground flex items-center gap-1.5 text-sm">
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

// ---- Health dashboard ----

function HealthDashboard({ health, syncRunning }: { health: ScreenerHealth; syncRunning: boolean }) {
  const { price, fundamentals, scores, universe_total } = health;

  const lastRun = scores.last_run_at ? new Date(scores.last_run_at) : null;
  const latestBar = price.latest_bar_date ? new Date(price.latest_bar_date + "T00:00:00") : null;

  const priceStale = latestBar ? (Date.now() - latestBar.getTime()) > 3 * 86_400_000 : true;
  const scoresStale = lastRun ? (Date.now() - lastRun.getTime()) > 3 * 86_400_000 : true;

  return (
    <div className="space-y-4">
      {/* 3-panel summary */}
      <div className="grid gap-3 sm:grid-cols-3">
        {/* Price data */}
        <StatusCard
          title="Price data"
          subtitle="Daily closing prices (adjusted)"
          good={price.symbols_with_recent_bars}
          total={universe_total}
          detail={latestBar ? `Last close: ${latestBar.toLocaleDateString("en-CA")}` : "No data yet"}
          stale={priceStale}
          staleness={latestBar ? `Last bar ${daysSince(latestBar)} ago` : undefined}
        />

        {/* Fundamentals */}
        <StatusCard
          title="Fundamental data"
          subtitle="Revenue, earnings, margins (SEC EDGAR)"
          good={fundamentals.symbols_with_fundamentals}
          total={scores.total_scored}
          detail={`${scores.total_scored - fundamentals.symbols_with_fundamentals} missing — many are Canadian (TSX-listed) and don't file with SEC`}
          stale={false}
          warn={fundamentals.pct_covered < 60}
        />

        {/* Scores */}
        <StatusCard
          title="Screener scores"
          subtitle="Trend Template + VCP + fundamentals ranked"
          good={scores.total_scored}
          total={universe_total}
          detail={lastRun ? `Scored ${timeAgo(lastRun)}` : "Never scored"}
          stale={scoresStale}
          staleness={lastRun ? `Run ${daysSince(lastRun)} day(s) ago` : undefined}
        />
      </div>

      {/* TT breakdown */}
      {Object.keys(scores.tt_distribution).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Trend Template breakdown</CardTitle>
            <CardDescription className="text-xs">
              How many stocks pass each number of criteria (0-8). Aim for TT ≥ 6 for quality setups.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <TTBar distribution={scores.tt_distribution} total={scores.total_scored} />
          </CardContent>
        </Card>
      )}

      {/* Missing fundamentals */}
      {fundamentals.top_missing.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              High-scoring stocks without fundamental data ({fundamentals.symbols_scored - fundamentals.symbols_with_fundamentals} total)
            </CardTitle>
            <CardDescription className="text-xs">{fundamentals.note}</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1.5">
              {fundamentals.top_missing.map(s => (
                <span key={s.symbol} className="bg-muted rounded px-2 py-1 text-xs font-mono">
                  {s.symbol}
                  <span className="text-muted-foreground ml-1">TT{s.tt_score}</span>
                </span>
              ))}
            </div>
            <p className="text-muted-foreground mt-3 text-xs">
              Run another scan to attempt fetching missing EDGAR data.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Missing price data */}
      {price.missing_symbols.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              Stocks with no recent price data ({price.missing_symbols.length})
            </CardTitle>
            <CardDescription className="text-xs">
              These are in your watchlist but yfinance returned no data. Likely delisted, renamed, or wrong ticker format.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1.5">
              {price.missing_symbols.map(s => (
                <span key={s} className="bg-muted text-muted-foreground rounded px-2 py-1 text-xs font-mono">{s}</span>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function StatusCard({
  title, subtitle, good, total, detail, stale, staleness, warn,
}: {
  title: string; subtitle: string;
  good: number; total: number;
  detail: string; stale?: boolean; staleness?: string; warn?: boolean;
}) {
  const pct = total > 0 ? Math.round(good / total * 100) : 0;
  const Icon = stale ? AlertTriangle : warn ? AlertTriangle : CheckCircle;
  const iconCls = stale ? "text-amber-500" : warn ? "text-amber-500" : good === total ? "text-emerald-500" : "text-emerald-500";

  return (
    <Card className={stale ? "border-amber-200 dark:border-amber-800" : ""}>
      <CardContent className="pt-4 pb-3 space-y-1.5">
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-sm font-semibold">{title}</p>
            <p className="text-muted-foreground text-xs">{subtitle}</p>
          </div>
          <Icon className={`h-4 w-4 shrink-0 mt-0.5 ${iconCls}`} />
        </div>
        <div className="flex items-end gap-2">
          <span className="text-2xl font-bold tabular-nums">{good.toLocaleString()}</span>
          <span className="text-muted-foreground text-sm pb-0.5">/ {total.toLocaleString()}</span>
          <span className="text-muted-foreground text-xs pb-0.5 ml-auto">{pct}%</span>
        </div>
        {/* Progress bar */}
        <div className="h-1.5 rounded-full bg-muted overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${stale ? "bg-amber-400" : "bg-emerald-500"}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <p className="text-muted-foreground text-xs">{detail}</p>
        {stale && staleness && (
          <p className="text-amber-600 dark:text-amber-400 text-xs font-medium">⚠ {staleness} — run a scan to refresh</p>
        )}
      </CardContent>
    </Card>
  );
}

function TTBar({ distribution, total }: { distribution: Record<string, number>; total: number }) {
  const scores = [8,7,6,5,4,3,2,1,0];
  const colors = ["bg-emerald-600","bg-emerald-500","bg-emerald-400","bg-amber-400","bg-amber-300","bg-amber-200","bg-muted","bg-muted","bg-muted"];

  return (
    <div className="space-y-1">
      {scores.map((s, i) => {
        const count = distribution[String(s)] ?? 0;
        if (count === 0) return null;
        const pct = total > 0 ? (count / total * 100) : 0;
        return (
          <div key={s} className="flex items-center gap-2 text-xs">
            <span className="w-10 text-right tabular-nums font-medium text-muted-foreground">{s}/8</span>
            <div className="flex-1 h-4 rounded bg-muted overflow-hidden">
              <div className={`h-full rounded ${colors[i]}`} style={{ width: `${Math.max(pct, 0.5)}%` }} />
            </div>
            <span className="w-16 tabular-nums text-muted-foreground">{count} ({pct.toFixed(0)}%)</span>
          </div>
        );
      })}
    </div>
  );
}

// ---- Filters ----

function FilterBar({
  currentMinTT, currentMinVCP, totalResults, page, pages,
}: {
  currentMinTT: number; currentMinVCP: number; totalResults: number; page: number; pages: number;
}) {
  const buildUrl = (p: number) => {
    const q = new URLSearchParams();
    if (currentMinTT)  q.set("min_tt",  String(currentMinTT));
    if (currentMinVCP) q.set("min_vcp", String(currentMinVCP));
    if (p > 1)         q.set("page",    String(p));
    return `/screener${q.size ? "?" + q : ""}`;
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-muted-foreground text-sm font-medium">
        {totalResults} candidates
        {pages > 1 && ` · page ${page}/${pages}`}
      </span>
      <form method="get" className="flex items-center gap-2 ml-auto">
        <label className="flex items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">Min Trend Template</span>
          <select name="min_tt" defaultValue={String(currentMinTT)}
            className="border-input bg-background h-7 rounded border px-2 text-xs">
            {[0,3,4,5,6,7,8].map(v => <option key={v} value={v}>{v}+/8</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs">
          <span className="text-muted-foreground">Min VCP</span>
          <select name="min_vcp" defaultValue={String(currentMinVCP)}
            className="border-input bg-background h-7 rounded border px-2 text-xs">
            {[[0,"Any"],[0.3,"3/10+"],[0.5,"5/10+"],[0.7,"7/10+"]].map(([v,l]) =>
              <option key={v} value={v}>{l}</option>
            )}
          </select>
        </label>
        <button type="submit" className="bg-muted hover:bg-muted/80 h-7 rounded px-3 text-xs font-medium">Apply</button>
        {(currentMinTT > 0 || currentMinVCP > 0) && (
          <Link href="/screener" className="text-muted-foreground text-xs hover:underline">Clear</Link>
        )}
      </form>

      {/* Pagination */}
      {pages > 1 && (
        <div className="flex items-center gap-1 ml-auto">
          {page > 1 && (
            <Link href={buildUrl(page - 1)} className="border-input hover:bg-muted inline-flex h-7 items-center rounded border px-2 text-xs">
              ← Prev
            </Link>
          )}
          <span className="text-muted-foreground text-xs px-1">{page} / {pages}</span>
          {page < pages && (
            <Link href={buildUrl(page + 1)} className="border-input hover:bg-muted inline-flex h-7 items-center rounded border px-2 text-xs">
              Next →
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

// ---- Result card ----

function ResultCard({ result: r }: { result: ScoreResult }) {
  const vcp = parseFloat(r.vcp_score);
  const fund = parseFloat(r.fundamental_score);
  const composite = Math.round(parseFloat(r.composite_score) * 100);

  const ttColor  = r.tt_score >= 7 ? "text-emerald-600 dark:text-emerald-400" : r.tt_score >= 5 ? "text-amber-600" : "text-muted-foreground";
  const vcpColor = vcp >= 0.7 ? "text-emerald-600 dark:text-emerald-400" : vcp >= 0.4 ? "text-amber-600" : "text-muted-foreground";
  const fundColor = fund >= 0.75 ? "text-emerald-600 dark:text-emerald-400" : fund >= 0.5 ? "text-amber-600" : "text-muted-foreground";

  const passing = Object.entries(r.tt_criteria).filter(([,v]) => v).map(([k]) => TT_CRITERIA_LABELS[k] ?? k);
  const failing  = Object.entries(r.tt_criteria).filter(([,v]) => !v).map(([k]) => TT_CRITERIA_LABELS[k] ?? k);

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xl font-semibold">{r.symbol}</span>
            {r.sector && <Badge variant="outline" className="text-[10px]">{r.sector}</Badge>}
            <Link href={`/chart/${r.symbol}`} className="text-muted-foreground hover:text-foreground" title="View chart">
              <BarChart2 className="h-3.5 w-3.5" />
            </Link>
            <Link href={`/tickets/new?symbol=${r.symbol}`} className="text-primary text-xs hover:underline">+ Ticket</Link>
          </div>
          <div className="flex items-center gap-4">
            <Score label="Trend" value={`${r.tt_score}/8`} color={ttColor} />
            <Score label="VCP" value={`${(vcp * 10).toFixed(1)}/10`} color={vcpColor} />
            {r.rs_rank !== null && <Score label="RS rank" value={String(r.rs_rank)} />}
            {fund > 0 ? (
              <Score label="Earnings" value={`${(fund * 4).toFixed(1)}/4`} color={fundColor} />
            ) : (
              <Score label="Earnings" value="—" color="text-muted-foreground" />
            )}
            <div className="text-center">
              <div className="text-xl font-bold">{composite}</div>
              <div className="text-muted-foreground text-[10px] uppercase">Score/100</div>
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {/* Prices */}
        {r.last_close && (
          <div className="grid grid-cols-4 gap-1 text-center text-xs">
            {[["Price", r.last_close], ["50-day avg", r.ma_50], ["150-day avg", r.ma_150], ["200-day avg", r.ma_200]].map(([label, val]) => (
              <div key={label as string}>
                <div className="text-muted-foreground">{label}</div>
                <div className="tabular-nums font-medium">{val ? `$${parseFloat(val as string).toFixed(2)}` : "—"}</div>
              </div>
            ))}
          </div>
        )}

        {/* Fundamentals — visual bars */}
        {(r.revenue_growth || r.net_income_growth || r.net_margin) ? (
          <div className="rounded-md bg-muted/30 px-3 py-2 space-y-1.5">
            <GrowthBar label="Rev growth"  value={r.revenue_growth}     target={0.25} />
            <GrowthBar label="EPS growth"  value={r.net_income_growth}  target={0.25} />
            <GrowthBar label="Net margin"  value={r.net_margin}         target={0.10} isMargin />
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">No fundamental data — Canadian (TSX) stocks don't file with SEC.</p>
        )}

        {/* TT criteria chips */}
        <div className="flex flex-wrap gap-1">
          {passing.map(c => (
            <span key={c} className="bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 rounded px-1.5 py-0.5 text-[10px]">✓ {c}</span>
          ))}
          {failing.map(c => (
            <span key={c} className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px]">· {c}</span>
          ))}
        </div>

        {/* VCP breakdown */}
        {r.vcp_details && (
          <div className="flex gap-2 text-center text-[10px]">
            {["tightness","compression","volume","pivot","trend"].map(k => {
              const val = r.vcp_details[k] as number;
              return (
                <div key={k} className="flex-1">
                  <div className={`font-semibold tabular-nums ${val >= 1.5 ? "text-emerald-600 dark:text-emerald-400" : val >= 0.5 ? "text-amber-600" : "text-muted-foreground"}`}>
                    {val?.toFixed(1) ?? "—"}
                  </div>
                  <div className="text-muted-foreground capitalize">{k}</div>
                </div>
              );
            })}
          </div>
        )}

        {/* Mini chart — 50 SMA (amber) + 150 SMA (violet) overlaid */}
        <StockChart symbol={r.symbol} height={180} mini showSmas className="rounded-md overflow-hidden" />

        <Link href={`/chart/${r.symbol}`} className="text-primary text-xs flex items-center gap-1 hover:underline">
          <BarChart2 className="h-3 w-3" /> View full chart with SMA overlays + pivot →
        </Link>
      </CardContent>
    </Card>
  );
}

function Score({ label, value, color = "" }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center">
      <div className={`text-lg font-bold tabular-nums ${color}`}>{value}</div>
      <div className="text-muted-foreground text-[10px] uppercase">{label}</div>
    </div>
  );
}

function GrowthBar({
  label, value, target, isMargin,
}: {
  label: string; value: string | null; target: number; isMargin?: boolean;
}) {
  if (!value) return null;
  const v = parseFloat(value);
  const passing = v >= target;
  const great   = v >= target * 2;
  // Cap visual bar at 200% of target for display
  const barPct  = Math.min(Math.max(v / (target * 2), 0), 1) * 100;
  const color   = great ? "bg-emerald-500" : passing ? "bg-emerald-400" : v > 0 ? "bg-amber-400" : "bg-destructive";
  const textCls = great ? "text-emerald-600 dark:text-emerald-400" : passing ? "text-emerald-600/70" : v > 0 ? "text-amber-600" : "text-destructive";

  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="text-muted-foreground w-20 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${barPct}%` }} />
      </div>
      <span className={`tabular-nums font-medium w-12 text-right ${textCls}`}>
        {v >= 0 ? "+" : ""}{(v * 100).toFixed(0)}%
      </span>
    </div>
  );
}

// ---- Helpers ----

function daysSince(d: Date): string {
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

function timeAgo(d: Date): string {
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)} days ago`;
}
