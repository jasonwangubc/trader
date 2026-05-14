"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Plus, ExternalLink, Sparkles, TrendingUp, Eye } from "lucide-react";
import { API_URL } from "@/lib/api";
import type { ScoreResult } from "@/lib/screener";
import { ExpandedDetails } from "./results-table";

interface PickRow {
  symbol: string;
  sector: string | null;
  last_close: string | null;
  pattern_type: string | null;
  pattern_quality: number;
  buyability: string;
  pivot_price: string | null;
  extension_pct: string | null;
  composite_score: number;
  eps_rank: number | null;
  rs_rank: number | null;
  accelerating: boolean;
  tier: string;
  reason: string;
}

interface PicksOut {
  tier_s: PickRow[];
  tier_a: PickRow[];
  tier_b: PickRow[];
  as_of: string | null;
  note: string;
}

export function TodaysPicks() {
  const [picks, setPicks] = useState<PicksOut | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [expandedSymbols, setExpandedSymbols] = useState<Set<string>>(new Set());
  const [details, setDetails] = useState<Record<string, ScoreResult>>({});
  const [loadingSymbols, setLoadingSymbols] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch(`${API_URL}/api/screener/picks`)
      .then(r => r.json())
      .then(setPicks)
      .catch(() => {});
  }, []);

  if (!picks) return null;
  const allPickRows = [...picks.tier_s, ...picks.tier_a, ...picks.tier_b];
  const total = allPickRows.length;
  if (total === 0) return null;

  const allSymbols = allPickRows.map(p => p.symbol);
  const allExpanded = allSymbols.length > 0 && allSymbols.every(s => expandedSymbols.has(s));

  const fetchDetail = async (symbol: string) => {
    if (details[symbol]) return;
    setLoadingSymbols(prev => new Set(prev).add(symbol));
    try {
      const res = await fetch(`${API_URL}/api/screener/score/${symbol}`);
      if (res.ok) {
        const data: ScoreResult = await res.json();
        setDetails(prev => ({ ...prev, [symbol]: data }));
      } else {
        console.error(`Score fetch ${symbol}: HTTP ${res.status}`);
      }
    } catch (e) {
      console.error(`Score fetch ${symbol}:`, e);
    } finally {
      setLoadingSymbols(prev => {
        const next = new Set(prev);
        next.delete(symbol);
        return next;
      });
    }
  };

  const toggleSymbol = (symbol: string) => {
    const willExpand = !expandedSymbols.has(symbol);
    setExpandedSymbols(prev => {
      const next = new Set(prev);
      willExpand ? next.add(symbol) : next.delete(symbol);
      return next;
    });
    if (willExpand) fetchDetail(symbol);
  };

  const toggleAll = () => {
    if (allExpanded) {
      setExpandedSymbols(new Set());
    } else {
      setExpandedSymbols(new Set(allSymbols));
      allSymbols.forEach(fetchDetail);
    }
  };

  return (
    <div className="rounded-xl border border-primary/30 bg-linear-to-br from-primary/5 to-card overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-primary/20">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <h2 className="text-sm font-semibold uppercase tracking-wide">Today's Picks</h2>
          <span className="text-xs text-muted-foreground">
            {total} curated · highest expected value first
          </span>
        </div>
        <div className="flex items-center gap-3">
          {!collapsed && (
            <button
              onClick={toggleAll}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              {allExpanded ? "Collapse all" : "Expand all"}
            </button>
          )}
          <button
            onClick={() => setCollapsed(c => !c)}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            {collapsed ? "Show" : "Hide"}
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="p-3 space-y-3">
          {picks.tier_s.length > 0 && (
            <TierBlock
              label="Tier S"
              sublabel="Highest EV — HTF / Ascending Triangle at pivot (take these always)"
              tone="emerald"
              picks={picks.tier_s}
              expandedSymbols={expandedSymbols}
              details={details}
              loadingSymbols={loadingSymbols}
              onToggle={toggleSymbol}
            />
          )}
          {picks.tier_a.length > 0 && (
            <TierBlock
              label="Tier A"
              sublabel="Quality bases at pivot, earnings accelerating"
              tone="amber"
              picks={picks.tier_a}
              expandedSymbols={expandedSymbols}
              details={details}
              loadingSymbols={loadingSymbols}
              onToggle={toggleSymbol}
            />
          )}
          {picks.tier_b.length > 0 && (
            <TierBlock
              label="Tier B"
              sublabel="In-base — watch for breakout"
              tone="sky"
              picks={picks.tier_b}
              expandedSymbols={expandedSymbols}
              details={details}
              loadingSymbols={loadingSymbols}
              onToggle={toggleSymbol}
            />
          )}
          <p className="text-[10px] text-muted-foreground/70 italic mt-1">
            {picks.note}
          </p>
        </div>
      )}
    </div>
  );
}

function TierBlock({
  label, sublabel, tone, picks,
  expandedSymbols, details, loadingSymbols, onToggle,
}: {
  label: string;
  sublabel: string;
  tone: "emerald" | "amber" | "sky";
  picks: PickRow[];
  expandedSymbols: Set<string>;
  details: Record<string, ScoreResult>;
  loadingSymbols: Set<string>;
  onToggle: (symbol: string) => void;
}) {
  const toneCls = {
    emerald: "border-emerald-500/40 bg-emerald-500/5",
    amber:   "border-amber-500/40 bg-amber-500/5",
    sky:     "border-sky-500/40 bg-sky-500/5",
  }[tone];
  const labelCls = {
    emerald: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
    amber:   "bg-amber-500/15 text-amber-400 border-amber-500/40",
    sky:     "bg-sky-500/15 text-sky-400 border-sky-500/40",
  }[tone];
  const Icon = tone === "emerald" ? Sparkles : tone === "amber" ? TrendingUp : Eye;

  return (
    <div className={`rounded-lg border ${toneCls} p-2.5`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold ${labelCls}`}>
          <Icon className="h-3 w-3" />
          {label}
        </span>
        <span className="text-[11px] text-muted-foreground">{sublabel}</span>
      </div>
      <div className="space-y-1">
        {picks.map(p => (
          <PickCard
            key={p.symbol}
            pick={p}
            expanded={expandedSymbols.has(p.symbol)}
            detail={details[p.symbol] ?? null}
            loading={loadingSymbols.has(p.symbol)}
            onToggle={() => onToggle(p.symbol)}
          />
        ))}
      </div>
    </div>
  );
}

function PickCard({
  pick: p, expanded, detail, loading, onToggle,
}: {
  pick: PickRow;
  expanded: boolean;
  detail: ScoreResult | null;
  loading: boolean;
  onToggle: () => void;
}) {
  const ext = p.extension_pct ? parseFloat(p.extension_pct) : null;

  return (
    <div className={`rounded-md border bg-card/80 transition-colors ${expanded ? "border-primary/40" : "border-border/40 hover:border-primary/30"}`}>
      <div
        className="flex items-center gap-3 px-2.5 py-1.5 cursor-pointer"
        onClick={onToggle}
      >
        <span className="text-muted-foreground shrink-0">
          {loading
            ? <span className="h-3.5 w-3.5 inline-block text-[10px] animate-pulse">…</span>
            : expanded
              ? <ChevronDown className="h-3.5 w-3.5" />
              : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
        <Link
          href={`/chart/${p.symbol}`}
          className="font-mono font-semibold text-sm w-16 hover:text-primary"
          onClick={e => e.stopPropagation()}
        >
          {p.symbol}
        </Link>
        <span className="text-[11px] text-muted-foreground truncate max-w-32 hidden md:inline">
          {p.sector ?? "—"}
        </span>
        <span className="text-xs tabular-nums w-16 text-right">
          {p.last_close ? `$${parseFloat(p.last_close).toFixed(2)}` : "—"}
        </span>
        <span className="text-[11px] text-muted-foreground flex-1 truncate">
          {p.reason}
        </span>
        <span className="text-xs tabular-nums w-14 text-right font-bold text-foreground/90">
          {p.composite_score.toFixed(0)}
        </span>
        {p.pivot_price && (
          <span className={`text-[10px] tabular-nums w-20 text-right ${
            ext === null ? "text-muted-foreground/60"
            : ext > 3    ? "text-amber-400"
            : ext > -3   ? "text-emerald-400"
            :              "text-sky-400"
          }`}>
            piv ${parseFloat(p.pivot_price).toFixed(2)}
          </span>
        )}
        <div className="flex items-center gap-1 shrink-0" onClick={e => e.stopPropagation()}>
          <Link
            href={`/tickets/new?symbol=${p.symbol}`}
            className="inline-flex h-6 items-center gap-0.5 rounded bg-primary/15 px-1.5 text-[10px] font-medium text-primary hover:bg-primary/25 transition-colors"
            title="Create ticket"
          >
            <Plus className="h-3 w-3" />
          </Link>
          <Link
            href={`/chart/${p.symbol}`}
            className="inline-flex h-6 w-6 items-center justify-center rounded border border-border/60 text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors"
            title="Open full chart"
          >
            <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
      </div>
      {expanded && detail && (
        <div className="px-4 py-4 border-t border-border/40 bg-muted/10">
          <ExpandedDetails r={detail} />
        </div>
      )}
      {expanded && !detail && !loading && (
        <div className="px-4 py-3 border-t border-border/40 text-xs text-muted-foreground/60">
          No score data — try refreshing or running a scan.
        </div>
      )}
    </div>
  );
}
