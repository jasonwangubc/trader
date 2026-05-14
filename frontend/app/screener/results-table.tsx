"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, ChevronUp, Plus, ExternalLink, Bookmark, BookmarkCheck } from "lucide-react";
import { API_URL } from "@/lib/api";
import { StockChart } from "@/components/stock-chart";
import {
  type ScoreResult,
  TT_CRITERIA_LABELS,
  PATTERN_LABELS,
  BUYABILITY_LABELS,
} from "@/lib/screener";

type SortKey =
  | "composite_score"
  | "eps_rank"
  | "rs_rank"
  | "vcp_score"
  | "tt_score"
  | "smr_rank"
  | "last_close"
  | "extension_pct"
  | "symbol"
  | "sector";

type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string; align?: "left" | "right" | "center"; width?: string; help?: string }[] = [
  { key: "symbol",          label: "Symbol",  align: "left",  width: "11rem", help: "Symbol — colored dot shows buyability (green=at pivot, blue=in base, amber=watch, red=broken)" },
  { key: "sector",          label: "Sector",  align: "left",  width: "9rem" },
  { key: "last_close",      label: "Price",   align: "right", width: "5rem" },
  { key: "extension_pct",   label: "Ext",     align: "right", width: "4.5rem", help: "Extension from pivot in %. Strict Minervini: >5% past pivot is not buyable." },
  { key: "composite_score", label: "Comp",    align: "right", width: "4.5rem", help: "Composite 0-100. Extended/broken stocks score 0." },
  { key: "eps_rank",        label: "EPS",     align: "right", width: "4rem",   help: "IBD-style EPS rank 0-99 (qtrly EPS growth + TTM EPS)" },
  { key: "rs_rank",         label: "RS",      align: "right", width: "4rem",   help: "Relative strength rank 0-99 (1Y return vs SPY)" },
  { key: "vcp_score",       label: "VCP",     align: "right", width: "4rem",   help: "VCP setup quality 0-10" },
  { key: "tt_score",        label: "Tech",    align: "right", width: "4rem",   help: "Trend Template 0-8" },
  { key: "smr_rank",        label: "SMR",     align: "right", width: "4rem",   help: "Sales/Margin/ROE rank 0-99" },
];

export function ResultsTable({ results }: { results: ScoreResult[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("composite_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const sorted = useMemo(() => {
    const arr = [...results];
    arr.sort((a, b) => {
      const va = sortVal(a, sortKey);
      const vb = sortVal(b, sortKey);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "string" && typeof vb === "string") {
        return sortDir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      return sortDir === "asc" ? (va as number) - (vb as number) : (vb as number) - (va as number);
    });
    return arr;
  }, [results, sortKey, sortDir]);

  const allExpanded = sorted.length > 0 && expanded.size === sorted.length;
  const toggleAll   = () => {
    if (allExpanded) {
      setExpanded(new Set());
    } else {
      setExpanded(new Set(sorted.map(r => r.symbol)));
    }
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Symbol/sector default to ascending alphabetical; everything else descending (high = good)
      setSortDir(key === "symbol" || key === "sector" ? "asc" : "desc");
    }
  };

  if (results.length === 0) {
    return (
      <div className="rounded-xl border bg-card p-12 text-center text-sm text-muted-foreground">
        No stocks match the current filters. Try lowering the minimums or clearing them.
      </div>
    );
  }

  return (
    <div className="rounded-xl border bg-card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/30 border-b border-border/60 sticky top-0 z-10">
            <tr>
              <th className="w-8 px-1">
                <button
                  onClick={toggleAll}
                  title={allExpanded ? "Collapse all" : "Expand all"}
                  className="inline-flex h-5 w-5 items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                >
                  {allExpanded
                    ? <ChevronDown className="h-3.5 w-3.5" />
                    : <ChevronRight className="h-3.5 w-3.5" />}
                </button>
              </th>
              {COLUMNS.map(c => (
                <th
                  key={c.key}
                  className={`px-2 py-2 text-[11px] font-medium uppercase tracking-wide text-muted-foreground select-none ${
                    c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left"
                  }`}
                  style={{ width: c.width }}
                  title={c.help}
                >
                  <button
                    onClick={() => toggleSort(c.key)}
                    className={`inline-flex items-center gap-1 hover:text-foreground transition-colors ${
                      sortKey === c.key ? "text-foreground" : ""
                    } ${c.align === "right" ? "flex-row-reverse" : ""}`}
                  >
                    {c.label}
                    {sortKey === c.key &&
                      (sortDir === "desc" ? <ChevronDown className="h-3 w-3" /> : <ChevronUp className="h-3 w-3" />)}
                  </button>
                </th>
              ))}
              <th className="w-24" />
            </tr>
          </thead>
          <tbody>
            {sorted.map(r => (
              <ResultRow
                key={r.symbol}
                r={r}
                expanded={expanded.has(r.symbol)}
                onToggle={() => setExpanded(prev => {
                  const next = new Set(prev);
                  next.has(r.symbol) ? next.delete(r.symbol) : next.add(r.symbol);
                  return next;
                })}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function sortVal(r: ScoreResult, key: SortKey): number | string | null {
  switch (key) {
    case "symbol":          return r.symbol;
    case "sector":          return r.sector ?? "";
    case "last_close":      return r.last_close ? parseFloat(r.last_close) : null;
    case "composite_score": return parseFloat(r.composite_score);
    case "eps_rank":        return r.eps_rank;
    case "rs_rank":         return r.rs_rank;
    case "vcp_score":       return parseFloat(r.vcp_score);
    case "tt_score":        return r.tt_score;
    case "smr_rank":        return r.smr_rank;
    case "extension_pct":   return r.extension_pct ? parseFloat(r.extension_pct) : null;
  }
}

function buyabilityDot(b: string | null): { color: string; label: string } {
  switch (b) {
    case "at_pivot": return { color: "bg-emerald-500",         label: "At pivot — buyable now" };
    case "in_base":  return { color: "bg-sky-500",             label: "In base — wait for breakout" };
    case "extended": return { color: "bg-rose-500",            label: "Extended past pivot — not buyable" };
    case "broken":   return { color: "bg-rose-700",            label: "Trend broken — exclude" };
    case "frozen":   return { color: "bg-slate-500",           label: "Frozen — likely acquisition target or halted (near-zero daily range)" };
    default:         return { color: "bg-muted-foreground/40", label: "No clean setup" };
  }
}

function ResultRow({ r, expanded, onToggle }: { r: ScoreResult; expanded: boolean; onToggle: () => void }) {
  const composite = Math.round(parseFloat(r.composite_score) * 100);
  const vcp10 = Math.round(parseFloat(r.vcp_score) * 10);
  const ext = r.extension_pct ? parseFloat(r.extension_pct) : null;
  const dot = buyabilityDot(r.buyability);
  const patternLabel = r.pattern_type ? PATTERN_LABELS[r.pattern_type] ?? r.pattern_type : null;
  const [watched, setWatched] = useState(false);
  const [watching, setWatching] = useState(false);

  const addToWatchlist = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (watched || watching) return;
    setWatching(true);
    try {
      await fetch(`${API_URL}/api/screener/watchlist`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ symbol: r.symbol }),
      });
      setWatched(true);
    } finally {
      setWatching(false);
    }
  };

  return (
    <>
      <tr
        className={`border-b border-border/40 cursor-pointer hover:bg-muted/30 transition-colors ${
          expanded ? "bg-muted/20" : ""
        }`}
        onClick={onToggle}
      >
        <td className="px-1 text-muted-foreground">
          {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </td>
        <td className="px-2 py-2">
          <div className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full shrink-0 ${dot.color}`} title={dot.label} />
            <span className="font-mono font-semibold">{r.symbol}</span>
            {patternLabel && (
              <span
                className="text-[9px] uppercase tracking-wide bg-primary/15 text-primary rounded px-1 py-0.5"
                title={`Pattern: ${patternLabel} · quality ${r.pattern_quality ? Math.round(parseFloat(r.pattern_quality) * 100) : 0}/100`}
              >
                {patternLabel}
              </span>
            )}
          </div>
        </td>
        <td className="px-2 py-2 text-muted-foreground text-xs truncate max-w-40">{r.sector ?? "—"}</td>
        <td className="px-2 py-2 text-right tabular-nums text-muted-foreground">
          {r.last_close ? `$${parseFloat(r.last_close).toFixed(2)}` : "—"}
        </td>
        <td className={`px-2 py-2 text-right tabular-nums ${
          ext === null ? "text-muted-foreground/60"
          : ext > 5    ? "text-rose-400 font-semibold"
          : ext > 0    ? "text-amber-400"
          : ext > -5   ? "text-emerald-400"
          :              "text-sky-400"
        }`}>
          {ext === null ? "—" : `${ext > 0 ? "+" : ""}${ext.toFixed(1)}%`}
        </td>
        <td className={`px-2 py-2 text-right tabular-nums font-bold ${rankColor(composite)}`}>{composite}</td>
        <td className={`px-2 py-2 text-right tabular-nums ${rankColor(r.eps_rank)}`}>{r.eps_rank ?? "—"}</td>
        <td className={`px-2 py-2 text-right tabular-nums ${rankColor(r.rs_rank)}`}>{r.rs_rank ?? "—"}</td>
        <td className={`px-2 py-2 text-right tabular-nums ${rankColor(vcp10 * 10)}`}>{vcp10}</td>
        <td className={`px-2 py-2 text-right tabular-nums ${ttColor(r.tt_score)}`}>{r.tt_score}/8</td>
        <td className={`px-2 py-2 text-right tabular-nums ${rankColor(r.smr_rank)}`}>{r.smr_rank ?? "—"}</td>
        <td className="px-2 py-2" onClick={e => e.stopPropagation()}>
          <div className="flex items-center justify-end gap-1.5">
            <button
              onClick={addToWatchlist}
              disabled={watching}
              title={watched ? "On watchlist" : "Add to watchlist"}
              className={`inline-flex h-6 w-6 items-center justify-center rounded border transition-colors ${
                watched
                  ? "border-primary/50 text-primary"
                  : "border-border/60 text-muted-foreground hover:text-foreground hover:border-primary/50"
              }`}
            >
              {watched ? <BookmarkCheck className="h-3 w-3" /> : <Bookmark className="h-3 w-3" />}
            </button>
            <Link
              href={`/tickets/new?symbol=${r.symbol}`}
              className="inline-flex h-6 items-center gap-0.5 rounded bg-primary/15 px-1.5 text-[10px] font-medium text-primary hover:bg-primary/25 transition-colors"
              title="Create ticket"
            >
              <Plus className="h-3 w-3" /> Ticket
            </Link>
            <Link
              href={`/chart/${r.symbol}`}
              className="inline-flex h-6 items-center gap-1 rounded border border-border/60 px-2 text-[10px] font-medium text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors"
            >
              <ExternalLink className="h-3 w-3" /> Chart
            </Link>
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-muted/10 border-b border-border/40">
          <td colSpan={12} className="px-4 py-4">
            <ExpandedDetails r={r} />
          </td>
        </tr>
      )}
    </>
  );
}

export function ExpandedDetails({ r }: { r: ScoreResult }) {
  const passing = Object.entries(r.tt_criteria).filter(([, v]) => v).map(([k]) => TT_CRITERIA_LABELS[k] ?? k);
  const failing = Object.entries(r.tt_criteria).filter(([, v]) => !v).map(([k]) => TT_CRITERIA_LABELS[k] ?? k);
  const dot = buyabilityDot(r.buyability);
  const buyabilityLabel = r.buyability ? BUYABILITY_LABELS[r.buyability] ?? r.buyability : "—";
  const patternLabel = r.pattern_type ? PATTERN_LABELS[r.pattern_type] ?? r.pattern_type : "no clean setup";

  // Acceleration: quarterly EPS growth > annual EPS growth by a meaningful margin
  const qGrowth = r.net_income_growth ? parseFloat(r.net_income_growth) : null;
  const aGrowth = r.earnings_annual_growth ? parseFloat(r.earnings_annual_growth) : null;
  const isAccelerating = qGrowth !== null && aGrowth !== null && qGrowth > aGrowth + 0.05 && qGrowth > 0.10;
  const isDecelerating = qGrowth !== null && aGrowth !== null && aGrowth > qGrowth + 0.05 && aGrowth > 0;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      {/* Chart — taller, 1yr fetch zoomed to last ~80 bars (~4 months) */}
      <div className="flex flex-col gap-2">
        <StockChart
          symbol={r.symbol}
          height={420}
          days={252}
          visibleDays={80}
          barSpacing={8}
          showSmas
          showPivot={false}
          className="rounded-lg overflow-hidden"
        />
        <div className="flex justify-end">
          <Link
            href={`/chart/${r.symbol}`}
            className="inline-flex items-center gap-1.5 rounded-md border border-border/60 bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground hover:border-primary/50 hover:text-primary transition-colors"
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Open full interactive chart
          </Link>
        </div>
      </div>

      {/* Side panel: setup, fundamentals, criteria */}
      <div className="space-y-3">
        <div className="rounded-lg border border-border/60 bg-card p-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Setup</h4>
          <div className="space-y-1.5 text-xs">
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${dot.color}`} />
              <span className="font-medium">{buyabilityLabel}</span>
              <span className="text-muted-foreground">·</span>
              <span className="text-muted-foreground">{patternLabel}</span>
              {r.pattern_quality && (
                <span className="text-muted-foreground/70">
                  ({Math.round(parseFloat(r.pattern_quality) * 100)}/100)
                </span>
              )}
            </div>
            {r.pivot_price && (
              <div className="grid grid-cols-2 gap-2 mt-2">
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Pivot (buy point)</div>
                  <div className="font-mono text-sm">${parseFloat(r.pivot_price).toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Extension</div>
                  <div className={`font-mono text-sm tabular-nums ${
                    r.extension_pct && parseFloat(r.extension_pct) > 5 ? "text-rose-400"
                    : r.extension_pct && parseFloat(r.extension_pct) >= -3 ? "text-emerald-400"
                    : "text-sky-400"
                  }`}>
                    {r.extension_pct ? `${parseFloat(r.extension_pct) > 0 ? "+" : ""}${parseFloat(r.extension_pct).toFixed(1)}%` : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Base low</div>
                  <div className="font-mono text-sm">{r.base_low ? `$${parseFloat(r.base_low).toFixed(2)}` : "—"}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Base depth</div>
                  <div className="font-mono text-sm">{r.base_depth_pct ? `${parseFloat(r.base_depth_pct).toFixed(1)}%` : "—"}</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">Base length</div>
                  <div className="font-mono text-sm">{r.base_length_days ? `${r.base_length_days}d` : "—"}</div>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="rounded-lg border border-border/60 bg-card p-3">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Fundamentals</h4>
            {isAccelerating && (
              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium text-emerald-400" title={`Q EPS ${qGrowth !== null ? (qGrowth*100).toFixed(0) : '?'}% vs Annual ${aGrowth !== null ? (aGrowth*100).toFixed(0) : '?'}% — earnings accelerating`}>
                ↑ Accelerating
              </span>
            )}
            {isDecelerating && (
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-400" title={`Q EPS ${qGrowth !== null ? (qGrowth*100).toFixed(0) : '?'}% vs Annual ${aGrowth !== null ? (aGrowth*100).toFixed(0) : '?'}% — earnings decelerating`}>
                ↓ Decelerating
              </span>
            )}
          </div>
          {(r.revenue_growth || r.net_income_growth || r.net_margin || r.roe) ? (
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
              <FundCell label="Rev growth (YoY)"   value={r.revenue_growth}           target={0.15} isGrowth />
              <FundCell label="EPS growth (Q YoY)" value={r.net_income_growth}        target={0.25} isGrowth
                hint={aGrowth !== null ? `Annual: ${aGrowth >= 0 ? "+" : ""}${(aGrowth * 100).toFixed(0)}%` : undefined} />
              <FundCell label="Net margin"          value={r.net_margin}               target={0.10} isGrowth={false} />
              <FundCell label="Return on equity"    value={r.roe}                      target={0.17} isGrowth={false} />
            </div>
          ) : (
            <p className="text-xs text-muted-foreground/70">No fundamental data.</p>
          )}
          {r.eps_ttm && (
            <p className="mt-2 text-[11px] text-muted-foreground">
              TTM EPS: <span className="font-mono text-foreground">${parseFloat(r.eps_ttm).toFixed(2)}</span>
            </p>
          )}
        </div>

        <div className="rounded-lg border border-border/60 bg-card p-3">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Trend template ({r.tt_score}/8)
          </h4>
          <div className="space-y-1">
            {passing.map(c => (
              <div key={c} className="flex items-center gap-1.5 text-[11px]">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                <span className="text-foreground/90">{c}</span>
              </div>
            ))}
            {failing.map(c => (
              <div key={c} className="flex items-center gap-1.5 text-[11px]">
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/30" />
                <span className="text-muted-foreground/60 line-through">{c}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function FundCell({
  label, value, target, isGrowth = true, hint,
}: {
  label: string;
  value: string | null;
  target: number;
  isGrowth?: boolean;   // true = growth rate (show +/-), false = level (e.g. margin, ROE)
  hint?: string;        // optional sub-label, e.g. annual comparison
}) {
  if (value == null) return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</div>
      <div className="font-mono text-muted-foreground/40">—</div>
    </div>
  );
  const v = parseFloat(value);
  const cls =
    v >= target * 2 ? "text-emerald-400" :
    v >= target     ? "text-emerald-400/80" :
    v >  0          ? "text-amber-400" :
                      "text-rose-400";

  // Growth rates show +/-; levels (margin, ROE) show plain %
  const formatted = isGrowth
    ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`
    : `${(v * 100).toFixed(1)}%`;

  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/60">{label}</div>
      <div className={`font-mono font-semibold tabular-nums text-sm ${cls}`}>{formatted}</div>
      {hint && <div className="text-[10px] text-muted-foreground/60 mt-0.5">{hint}</div>}
    </div>
  );
}

// Color a 0-99 percentile cell by tier (IBD-style)
function rankColor(v: number | null): string {
  if (v == null) return "text-muted-foreground/40";
  if (v >= 80) return "text-emerald-400 font-semibold";
  if (v >= 60) return "text-emerald-400/80";
  if (v >= 40) return "text-amber-400";
  return "text-muted-foreground";
}

function ttColor(v: number): string {
  if (v >= 7) return "text-emerald-400 font-semibold";
  if (v >= 5) return "text-amber-400";
  return "text-muted-foreground";
}
