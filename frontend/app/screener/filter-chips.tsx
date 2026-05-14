"use client";

import { useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { X } from "lucide-react";

// ── Chip definitions ─────────────────────────────────────────────────────────

/** Pattern chips — multi-selectable; builds a comma-joined `pattern=` param. */
const PATTERN_CHIPS: { label: string; value: string; hint: string }[] = [
  { label: "HTF",          value: "high_tight_flag",    hint: "High Tight Flag — highest EV (~6.9R)" },
  { label: "Asc Triangle", value: "ascending_triangle", hint: "Ascending Triangle (~68% win)" },
  { label: "VCP",          value: "vcp",                hint: "Volatility Contraction Pattern" },
  { label: "Cup & Handle", value: "cwh",                hint: "Cup with Handle" },
  { label: "Flat Base",    value: "flat_base",          hint: "Flat Base — tight sideways" },
  { label: "3WT",          value: "three_weeks_tight",  hint: "Three Weeks Tight (IBD)" },
  { label: "Bull Flag",    value: "bull_flag",          hint: "Bull Flag — mini-HTF" },
];

/** Threshold chips — each toggles a single numeric param. */
const THRESHOLD_CHIPS: { label: string; key: string; value: string; hint: string }[] = [
  { label: "EPS ≥ 80",  key: "min_eps",       value: "80", hint: "Top quintile quarterly EPS growth" },
  { label: "RS ≥ 80",   key: "min_rs",        value: "80", hint: "Top quintile relative strength vs SPY" },
  { label: "Leaders",   key: "min_composite", value: "70", hint: "Composite score ≥ 70" },
];

/** Preset chips — each replaces all params at once. */
const PRESET_CHIPS: { label: string; params: Record<string, string>; hint: string }[] = [
  { label: "All",        params: {},                                         hint: "Clear all filters" },
  { label: "Setting up", params: { buyability: "at_pivot,in_base" },        hint: "At pivot or in base — actionable setups" },
  { label: "At pivot",   params: { buyability: "at_pivot" },                hint: "Within ±5% of pivot — buyable today" },
  { label: "CANSLIM",    params: { min_eps: "75", min_rs: "75", buyability: "at_pivot,in_base" }, hint: "EPS + RS top quartile + clean setup" },
];

// ── Component ────────────────────────────────────────────────────────────────

export function FilterChips({ totalResults }: { totalResults: number }) {
  const router      = useRouter();
  const searchParams = useSearchParams();

  const activePatterns = (searchParams.get("pattern") ?? "").split(",").filter(Boolean);
  const activeBuyability = searchParams.get("buyability") ?? "";
  const activeMinEps  = searchParams.get("min_eps")       ?? "";
  const activeMinRs   = searchParams.get("min_rs")        ?? "";
  const activeMinComp = searchParams.get("min_composite") ?? "";

  const hasAnyFilter =
    activePatterns.length > 0 || activeBuyability || activeMinEps || activeMinRs || activeMinComp;

  /** Push a modified copy of the current params */
  const push = useCallback((modifier: (q: URLSearchParams) => void) => {
    const q = new URLSearchParams(searchParams.toString());
    q.delete("page");
    modifier(q);
    router.push(`/screener${q.size ? "?" + q : ""}`);
  }, [searchParams, router]);

  const togglePattern = (value: string) => {
    push(q => {
      const patterns = (q.get("pattern") ?? "").split(",").filter(Boolean);
      const idx = patterns.indexOf(value);
      if (idx >= 0) {
        patterns.splice(idx, 1);
      } else {
        patterns.push(value);
        // Auto-restrict to buyable setups when any pattern is first activated
        if (!q.get("buyability")) q.set("buyability", "at_pivot,in_base");
      }
      if (patterns.length > 0) q.set("pattern", patterns.join(","));
      else { q.delete("pattern"); q.delete("buyability"); }
    });
  };

  const toggleThreshold = (key: string, value: string) => {
    push(q => {
      if (q.get(key) === value) q.delete(key); else q.set(key, value);
    });
  };

  const setPreset = (params: Record<string, string>) => {
    const q = new URLSearchParams(params);
    router.push(`/screener${q.size ? "?" + q : ""}`);
  };

  // Count active selections for the summary
  const activePatternLabels = activePatterns
    .map(v => PATTERN_CHIPS.find(c => c.value === v)?.label ?? v);

  return (
    <div className="space-y-2">
      {/* Count + clear row */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs font-medium">
          {totalResults.toLocaleString()} {totalResults === 1 ? "stock" : "stocks"}
        </span>
        {hasAnyFilter && (
          <button
            onClick={() => router.push("/screener")}
            className="inline-flex items-center gap-1 rounded-full border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          >
            <X className="h-3 w-3" /> Clear all
          </button>
        )}
      </div>

      {/* Preset row */}
      <div className="flex flex-wrap gap-1.5">
        {PRESET_CHIPS.map(chip => {
          const active = !hasAnyFilter && Object.keys(chip.params).length === 0
            ? true // "All" is active when no filters
            : Object.entries(chip.params).every(([k, v]) => {
                const cur = searchParams.get(k) ?? "";
                return cur === v;
              }) && activePatterns.length === 0;
          return (
            <button
              key={chip.label}
              onClick={() => setPreset(chip.params)}
              title={chip.hint}
              className={`inline-flex h-7 items-center rounded-full border px-3 text-xs font-medium transition-colors ${
                active
                  ? "border-primary/50 bg-primary/15 text-primary"
                  : "border-border/60 text-muted-foreground hover:border-border hover:text-foreground"
              }`}
            >
              {chip.label}
            </button>
          );
        })}

        <span className="border-l border-border/40 mx-0.5" />

        {/* Pattern chips — multi-select */}
        {PATTERN_CHIPS.map(chip => {
          const active = activePatterns.includes(chip.value);
          return (
            <button
              key={chip.label}
              onClick={() => togglePattern(chip.value)}
              title={chip.hint + (active ? " — click to remove" : " — click to add")}
              className={`inline-flex h-7 items-center gap-1 rounded-full border px-3 text-xs font-medium transition-colors ${
                active
                  ? "border-primary/50 bg-primary/15 text-primary"
                  : "border-border/60 text-muted-foreground hover:border-border hover:text-foreground"
              }`}
            >
              {chip.label}
              {active && <X className="h-3 w-3 opacity-70" />}
            </button>
          );
        })}

        <span className="border-l border-border/40 mx-0.5" />

        {/* Threshold chips — independently toggleable */}
        {THRESHOLD_CHIPS.map(chip => {
          const active = searchParams.get(chip.key) === chip.value;
          return (
            <button
              key={chip.label}
              onClick={() => toggleThreshold(chip.key, chip.value)}
              title={chip.hint + (active ? " — click to remove" : " — click to add")}
              className={`inline-flex h-7 items-center gap-1 rounded-full border px-3 text-xs font-medium transition-colors ${
                active
                  ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-400"
                  : "border-border/60 text-muted-foreground hover:border-border hover:text-foreground"
              }`}
            >
              {chip.label}
              {active && <X className="h-3 w-3 opacity-70" />}
            </button>
          );
        })}
      </div>

      {/* Active selection summary */}
      {(activePatternLabels.length > 1 || (activePatternLabels.length > 0 && (activeMinEps || activeMinRs || activeMinComp))) && (
        <div className="text-[11px] text-muted-foreground">
          Showing: {[
            activePatternLabels.length > 0 && `patterns: ${activePatternLabels.join(" + ")}`,
            activeMinEps && `EPS ≥ ${activeMinEps}`,
            activeMinRs  && `RS ≥ ${activeMinRs}`,
            activeMinComp && `Composite ≥ ${activeMinComp}`,
            activeBuyability && activeBuyability.replace(",", " or ").replace(/_/g, " "),
          ].filter(Boolean).join(" · ")}
        </div>
      )}
    </div>
  );
}
