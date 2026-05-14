import Link from "next/link";
import { BarChart2, Plus, Target } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { StockChart } from "@/components/stock-chart";
import { WatchlistAdd } from "./watchlist-add";

export const metadata = { title: 'Watchlist' };


/**
 * Watchlist — intermediate state between "in screener universe" and "armed ticket".
 * Symbols you're tracking closely but haven't pre-committed to yet.
 *
 * Note: we reuse the screener_symbols table — the watchlist IS the screener universe.
 * The difference from the screener view is we show the chart and earnings for each
 * symbol, and the form is focused on "why I'm watching this" rather than scoring.
 *
 * In the future we could add a separate watchlist table with alert prices, but for
 * now this is sufficient: the monitor already polls for trigger conditions on ARMED
 * tickets, and this page is for visual review before committing.
 */

interface ScoreResult {
  symbol: string;
  tt_score: number;
  vcp_score: string;
  rs_rank: number | null;
  composite_score: string;
  sector: string | null;
  last_close: string | null;
}

interface EarningsInfo {
  symbol: string;
  next_earnings_date: string | null;
  days_until: number | null;
  last_eps_surprise_pct: string | null;
  warning: string | null;
}

export default async function WatchlistPage() {
  let results: ScoreResult[] = [];
  let earningsMap: Record<string, EarningsInfo> = {};
  let error: string | null = null;

  try {
    // Top-scored symbols = primary watchlist
    const [res, earningsList] = await Promise.all([
      api<{ items: ScoreResult[] }>("/api/screener/results?min_tt=5&page_size=30"),
      api<EarningsInfo[]>("/api/earnings").catch(() => [] as EarningsInfo[]),
    ]);
    results = res.items;
    earningsMap = Object.fromEntries(earningsList.map(e => [e.symbol, e]));
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  return (
    <main className="container mx-auto max-w-6xl p-6 sm:p-10">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Watchlist</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Top setups from the screener — visually confirm before arming a ticket.
            Run the screener scan to refresh.
          </p>
        </div>
        <div className="flex gap-2">
          <Link href="/screener/scan" className="border-input hover:bg-muted inline-flex h-9 items-center rounded-md border px-4 text-sm font-medium">
            Refresh screener
          </Link>
        </div>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">{error}</div>
      )}

      {results.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center text-sm text-muted-foreground">
            No setups yet. <Link href="/screener/scan" className="text-primary hover:underline">Run a screener scan</Link> first.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {results.map(r => {
            const earnings = earningsMap[r.symbol];
            return (
              <WatchlistCard
                key={r.symbol}
                result={r}
                earnings={earnings}
              />
            );
          })}
        </div>
      )}
    </main>
  );
}

function WatchlistCard({ result: r, earnings }: { result: ScoreResult; earnings?: EarningsInfo }) {
  const composite = Math.round(parseFloat(r.composite_score) * 100);
  const daysUntilEarnings = earnings?.days_until;
  const earningsWarning = earnings?.warning;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="font-mono text-2xl font-bold">{r.symbol}</span>
            {r.sector && <Badge variant="outline" className="text-xs">{r.sector}</Badge>}
            {r.last_close && (
              <span className="text-lg font-semibold text-muted-foreground">${parseFloat(r.last_close).toFixed(2)}</span>
            )}
            {earningsWarning && (
              <span className="rounded bg-amber-100 dark:bg-amber-900/30 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300">
                📅 Earnings in {daysUntilEarnings}d
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="text-center">
              <div className="text-xl font-bold">{composite}</div>
              <div className="text-muted-foreground text-[10px] uppercase">Score</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold">{r.tt_score}/8</div>
              <div className="text-muted-foreground text-[10px] uppercase">TT</div>
            </div>
            {r.rs_rank !== null && (
              <div className="text-center">
                <div className="text-xl font-bold">{r.rs_rank}</div>
                <div className="text-muted-foreground text-[10px] uppercase">RS</div>
              </div>
            )}
            <Link
              href={`/chart/${r.symbol}`}
              className="border-input hover:bg-muted inline-flex h-8 items-center gap-1 rounded-md border px-3 text-xs"
            >
              <BarChart2 className="h-3.5 w-3.5" /> Chart
            </Link>
            <Link
              href={`/tickets/new?symbol=${r.symbol}`}
              className="bg-primary text-primary-foreground inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium hover:bg-primary/90"
            >
              <Target className="h-3.5 w-3.5" /> Arm ticket
            </Link>
          </div>
        </div>
        {earningsWarning && (
          <div className="mt-2 rounded border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/20 px-3 py-1.5 text-xs text-amber-700 dark:text-amber-300">
            {earningsWarning}
          </div>
        )}
      </CardHeader>
      <CardContent>
        <StockChart symbol={r.symbol} height={280} showPivot showSmas className="w-full" />
      </CardContent>
    </Card>
  );
}
