"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight } from "lucide-react";
import type { ScreenerHealth } from "./page";

export function DataStatusPanel({ health }: { health: ScreenerHealth }) {
  const [open, setOpen] = useState(false);

  const { price, fundamentals, scores, universe_total } = health;
  const lastRun = scores.last_run_at ? new Date(scores.last_run_at) : null;
  const scoresStale = lastRun ? Date.now() - lastRun.getTime() > 3 * 86_400_000 : true;
  const anyIssue = price.is_stale || scoresStale || price.missing_symbols.length > 0;

  return (
    <details
      className="rounded-lg border border-border/60 bg-card/50"
      open={open}
      onToggle={e => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs hover:bg-muted/30 transition-colors">
        <span className="flex items-center gap-2">
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          <span className="font-medium uppercase tracking-wide text-muted-foreground">Data status</span>
          {anyIssue ? (
            <span className="inline-flex items-center gap-1 text-amber-500">
              <AlertTriangle className="h-3 w-3" />
              Issues
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-emerald-500">
              <CheckCircle className="h-3 w-3" />
              Healthy
            </span>
          )}
        </span>
        <span className="text-muted-foreground">
          {scores.total_scored.toLocaleString()} / {universe_total.toLocaleString()} scored
        </span>
      </summary>
      <div className="border-t border-border/40 px-3 py-3 space-y-3 text-xs">
        <div className="grid gap-2 sm:grid-cols-3">
          <Stat
            label="Price coverage"
            value={`${price.symbols_with_recent_bars.toLocaleString()} / ${universe_total.toLocaleString()}`}
            pct={universe_total > 0 ? (price.symbols_with_recent_bars / universe_total) * 100 : 0}
            warn={price.is_stale}
            sub={price.latest_bar_date ? `Last close: ${price.latest_bar_date}` : "No data"}
          />
          <Stat
            label="Fundamentals"
            value={`${fundamentals.symbols_with_fundamentals.toLocaleString()} / ${scores.total_scored.toLocaleString()}`}
            pct={fundamentals.pct_covered}
            sub={fundamentals.note}
          />
          <Stat
            label="Last scored"
            value={lastRun ? timeAgo(lastRun) : "Never"}
            pct={scoresStale ? 0 : 100}
            warn={scoresStale}
            sub={lastRun ? lastRun.toLocaleString("en-CA") : "Run a scan to populate"}
          />
        </div>

        {Object.keys(scores.tt_distribution).length > 0 && (
          <div>
            <h4 className="mb-1 font-medium text-muted-foreground">Trend Template distribution</h4>
            <TTBar distribution={scores.tt_distribution} total={scores.total_scored} />
          </div>
        )}

        {price.missing_symbols.length > 0 && (
          <div>
            <h4 className="mb-1 font-medium text-muted-foreground">
              No price data ({price.missing_symbols.length})
            </h4>
            <div className="flex flex-wrap gap-1">
              {price.missing_symbols.slice(0, 30).map(s => (
                <span key={s} className="bg-muted text-muted-foreground rounded px-1.5 py-0.5 text-[10px] font-mono">{s}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    </details>
  );
}

function Stat({
  label, value, pct, warn, sub,
}: {
  label: string; value: string; pct: number; warn?: boolean; sub?: string;
}) {
  return (
    <div className="rounded border border-border/40 bg-muted/10 p-2">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${warn ? "text-amber-500" : ""}`}>{value}</div>
      <div className="mt-1.5 h-1 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full ${warn ? "bg-amber-400" : "bg-emerald-500"}`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
      {sub && <div className="mt-1 text-[10px] text-muted-foreground/70">{sub}</div>}
    </div>
  );
}

function TTBar({ distribution, total }: { distribution: Record<string, number>; total: number }) {
  const buckets = [8, 7, 6, 5, 4, 3, 2, 1, 0];
  const colors = ["bg-emerald-600","bg-emerald-500","bg-emerald-400","bg-amber-400","bg-amber-300","bg-amber-200","bg-muted","bg-muted","bg-muted"];

  return (
    <div className="space-y-0.5">
      {buckets.map((s, i) => {
        const count = distribution[String(s)] ?? 0;
        if (count === 0) return null;
        const pct = total > 0 ? (count / total) * 100 : 0;
        return (
          <div key={s} className="flex items-center gap-2 text-[11px]">
            <span className="w-8 text-right tabular-nums text-muted-foreground">{s}/8</span>
            <div className="flex-1 h-3 rounded bg-muted overflow-hidden">
              <div className={`h-full rounded ${colors[i]}`} style={{ width: `${Math.max(pct, 0.5)}%` }} />
            </div>
            <span className="w-20 tabular-nums text-muted-foreground">{count} ({pct.toFixed(0)}%)</span>
          </div>
        );
      })}
    </div>
  );
}

function timeAgo(d: Date): string {
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)} days ago`;
}
