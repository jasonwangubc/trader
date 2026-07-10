import Link from "next/link";
import { BarChart2, Target } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { StockChart } from "@/components/stock-chart";
import { WatchlistAdd } from "./watchlist-add";
import { WatchlistRemoveButton } from "./watchlist-remove-button";

export const metadata = { title: "Watchlist" };

/**
 * Stage-2 pivot watchlist — persisted bridge between "screener says good"
 * (Tier S/A picks) and "I armed a ticket." Auto-synced nightly; also
 * supports manual add. Purely informational — arming a ticket (size, stop,
 * target) remains a separate, deliberate step.
 *
 * NOT the same table as /api/screener/watchlist (ScreenerSymbol, the nightly
 * scan universe) — see the cross-reference note in app/api/screener.py.
 */

interface WatchlistItem {
  id: string;
  symbol: string;
  sector: string | null;
  pivot_price: string;
  source: string;
  pattern_type: string | null;
  status: string;
  added_at: string;
  status_changed_at: string;
  ticket_id: string | null;
  notes: string | null;
  last_close: string | null;
  extension_pct: string | null;
  buyability: string | null;
  composite_score: number | null;
}

const STATUS_ORDER = ["at_pivot", "near_pivot", "watching", "armed", "extended", "broken"];

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  watching:   { label: "Watching",    className: "bg-muted text-muted-foreground" },
  near_pivot: { label: "Near pivot",  className: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300" },
  at_pivot:   { label: "At pivot",    className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300" },
  extended:   { label: "Extended",    className: "bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300" },
  broken:     { label: "Broken",      className: "bg-destructive/10 text-destructive" },
  armed:      { label: "Armed",       className: "bg-primary/10 text-primary" },
};

export default async function WatchlistPage() {
  let items: WatchlistItem[] = [];
  let error: string | null = null;

  try {
    items = await api<WatchlistItem[]>("/api/watchlist");
    items = [...items].sort(
      (a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status)
        || (a.symbol < b.symbol ? -1 : 1),
    );
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  return (
    <main className="container mx-auto max-w-6xl p-6 sm:p-10">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight">Watchlist</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Tier S/A picks are added automatically every night with their pivot locked in.
          You&apos;ll get an alert as one approaches its pivot with rising volume — arm a
          ticket when you want to act on it.
        </p>
      </header>

      <div className="mb-6">
        <WatchlistAdd />
      </div>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">{error}</div>
      )}

      {!error && items.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center text-sm text-muted-foreground">
            Nothing on your watchlist yet. Tier S/A picks are added automatically after the
            next nightly scan, or add a symbol manually above.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {items.map((item) => (
            <WatchlistCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </main>
  );
}

function WatchlistCard({ item }: { item: WatchlistItem }) {
  const statusStyle = STATUS_STYLE[item.status] ?? { label: item.status, className: "bg-muted" };
  const lastClose = item.last_close ? parseFloat(item.last_close) : null;
  const pivot = parseFloat(item.pivot_price);
  const extension = item.extension_pct !== null ? parseFloat(item.extension_pct) : null;
  const isArmed = item.status === "armed";
  const isTerminal = item.status === "broken";

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="font-mono text-2xl font-bold">{item.symbol}</span>
            {item.sector && <Badge variant="outline" className="text-xs">{item.sector}</Badge>}
            {lastClose !== null && (
              <span className="text-lg font-semibold text-muted-foreground">${lastClose.toFixed(2)}</span>
            )}
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusStyle.className}`}>
              {statusStyle.label}
            </span>
            {item.source !== "manual" && (
              <span className="text-muted-foreground text-[10px] uppercase tracking-wide">
                Tier {item.source === "tier_s" ? "S" : "A"}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <div className="text-center">
              <div className="text-lg font-bold">${pivot.toFixed(2)}</div>
              <div className="text-muted-foreground text-[10px] uppercase" title="Locked when this symbol was added to the watchlist — does not drift with nightly rescans">
                Pivot
              </div>
            </div>
            {extension !== null && (
              <div className="text-center">
                <div className="text-lg font-bold">{extension > 0 ? "+" : ""}{extension.toFixed(1)}%</div>
                <div className="text-muted-foreground text-[10px] uppercase">From pivot</div>
              </div>
            )}
            <Link
              href={`/chart/${item.symbol}`}
              className="border-input hover:bg-muted inline-flex h-8 items-center gap-1 rounded-md border px-3 text-xs"
            >
              <BarChart2 className="h-3.5 w-3.5" /> Chart
            </Link>
            {isArmed && item.ticket_id ? (
              <Link
                href={`/tickets/${item.ticket_id}`}
                className="bg-primary text-primary-foreground inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium hover:bg-primary/90"
              >
                <Target className="h-3.5 w-3.5" /> View ticket
              </Link>
            ) : (
              <Link
                href={`/tickets/new?symbol=${item.symbol}&trigger=${item.pivot_price}&watchlist_item_id=${item.id}`}
                className="bg-primary text-primary-foreground inline-flex h-8 items-center gap-1 rounded-md px-3 text-xs font-medium hover:bg-primary/90"
              >
                <Target className="h-3.5 w-3.5" /> Arm ticket
              </Link>
            )}
            {!isArmed && <WatchlistRemoveButton itemId={item.id} symbol={item.symbol} />}
          </div>
        </div>
        {isTerminal && (
          <div className="mt-2 rounded border border-destructive/30 bg-destructive/5 px-3 py-1.5 text-xs text-destructive">
            Base invalidated since this was added — no longer a live setup. Remove or keep for reference.
          </div>
        )}
      </CardHeader>
      <CardContent>
        <StockChart symbol={item.symbol} height={280} showPivot showSmas className="w-full" />
      </CardContent>
    </Card>
  );
}
