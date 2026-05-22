"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp, ChevronsUpDown, AlertTriangle } from "lucide-react";
import type { WheelCandidate, CorrelationReport } from "@/lib/wheel";
import { money, num, pct } from "@/lib/wheel";
import { API_URL } from "@/lib/api";

type SortKey = keyof Pick<
  WheelCandidate,
  "score" | "annualized_yield_pct" | "premium_yield_pct" | "otm_pct" | "open_interest" | "dte" | "symbol"
>;

const SORTABLE_NUMERIC: Set<SortKey> = new Set([
  "score", "annualized_yield_pct", "premium_yield_pct", "otm_pct", "open_interest", "dte",
]);

export function CandidatesView({ candidates }: { candidates: WheelCandidate[] }) {
  const [strategy, setStrategy] = useState<"all" | "csp" | "cc">("all");
  const [minYield, setMinYield] = useState(0.10);
  const [skipEarnings, setSkipEarnings] = useState(true);
  const [sectorFilter, setSectorFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [basket, setBasket] = useState<Set<string>>(new Set());

  const sectors = useMemo(() => {
    const set = new Set<string>();
    candidates.forEach(c => { if (c.sector) set.add(c.sector); });
    return Array.from(set).sort();
  }, [candidates]);

  const filtered = useMemo(() => {
    return candidates.filter(c => {
      if (strategy !== "all" && c.strategy !== strategy) return false;
      if (parseFloat(c.annualized_yield_pct) < minYield) return false;
      if (skipEarnings && c.earnings_before_expiry) return false;
      if (sectorFilter !== "all" && c.sector !== sectorFilter) return false;
      return true;
    });
  }, [candidates, strategy, minYield, skipEarnings, sectorFilter]);

  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      if (sortKey === "symbol") {
        return sortDir === "asc" ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol);
      }
      const va = parseFloat(a[sortKey] as string);
      const vb = parseFloat(b[sortKey] as string);
      if (isNaN(va) && isNaN(vb)) return 0;
      if (isNaN(va)) return 1;
      if (isNaN(vb)) return -1;
      return sortDir === "asc" ? va - vb : vb - va;
    });
    return arr;
  }, [filtered, sortKey, sortDir]);

  const toggleSort = (k: SortKey) => {
    if (sortKey === k) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortKey(k);
      setSortDir(SORTABLE_NUMERIC.has(k) ? "desc" : "asc");
    }
  };

  const toggleBasket = (id: string) => {
    setBasket(b => {
      const next = new Set(b);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const basketCandidates = useMemo(() => candidates.filter(c => basket.has(c.id)), [candidates, basket]);

  return (
    <div className="space-y-3">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-background/40 p-3 text-xs">
        <FilterChip
          active={strategy === "all"} onClick={() => setStrategy("all")} label="All" />
        <FilterChip
          active={strategy === "csp"} onClick={() => setStrategy("csp")} label="CSPs" />
        <FilterChip
          active={strategy === "cc"} onClick={() => setStrategy("cc")} label="Covered calls" />
        <span className="text-muted-foreground/60 mx-1">·</span>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <span className="text-muted-foreground">Min annualized</span>
          <select
            value={minYield}
            onChange={e => setMinYield(parseFloat(e.target.value))}
            className="border-input bg-background h-7 rounded border px-2 tabular-nums"
          >
            <option value={0}>0%</option>
            <option value={0.05}>5%</option>
            <option value={0.10}>10%</option>
            <option value={0.15}>15%</option>
            <option value={0.20}>20%</option>
            <option value={0.30}>30%</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input type="checkbox" checked={skipEarnings} onChange={e => setSkipEarnings(e.target.checked)} />
          <span>Skip earnings-window</span>
        </label>
        <span className="text-muted-foreground/60 mx-1">·</span>
        <label className="flex items-center gap-1.5">
          <span className="text-muted-foreground">Sector</span>
          <select
            value={sectorFilter}
            onChange={e => setSectorFilter(e.target.value)}
            className="border-input bg-background h-7 rounded border px-2"
          >
            <option value="all">All</option>
            {sectors.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <span className="ml-auto text-muted-foreground">
          {sorted.length} of {candidates.length} candidates
        </span>
      </div>

      {/* Basket bar */}
      {basket.size > 0 && (
        <BasketCorrelation
          symbols={basketCandidates}
          onClear={() => setBasket(new Set())}
        />
      )}

      {/* Table */}
      <div className="rounded-lg border overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <Th width="2rem" />
              <Th label="Sym"     onClick={() => toggleSort("symbol")}    active={sortKey === "symbol"}    dir={sortDir} />
              <Th label="Strat"   />
              <Th label="Sector"  align="left" />
              <Th label="Spot"    align="right" />
              <Th label="Strike"  align="right" />
              <Th label="OTM %"   align="right" onClick={() => toggleSort("otm_pct")} active={sortKey === "otm_pct"} dir={sortDir} />
              <Th label="DTE"     align="right" onClick={() => toggleSort("dte")} active={sortKey === "dte"} dir={sortDir} />
              <Th label="Mid"     align="right" />
              <Th label="Yield"   align="right" help="Premium / capital-at-risk" onClick={() => toggleSort("premium_yield_pct")} active={sortKey === "premium_yield_pct"} dir={sortDir} />
              <Th label="Ann."    align="right" help="Annualized: yield × 365/DTE" onClick={() => toggleSort("annualized_yield_pct")} active={sortKey === "annualized_yield_pct"} dir={sortDir} />
              <Th label="Δ"       align="right" help="Approx |delta|" />
              <Th label="OI"      align="right" onClick={() => toggleSort("open_interest")} active={sortKey === "open_interest"} dir={sortDir} />
              <Th label="Spread"  align="right" help="Bid-ask spread / mid" />
              <Th label="Capital" align="right" />
              <Th label="Score"   align="right" onClick={() => toggleSort("score")} active={sortKey === "score"} dir={sortDir} />
            </tr>
          </thead>
          <tbody>
            {sorted.map(c => (
              <Row
                key={c.id}
                c={c}
                inBasket={basket.has(c.id)}
                onToggle={() => toggleBasket(c.id)}
              />
            ))}
            {sorted.length === 0 && (
              <tr>
                <td colSpan={16} className="text-muted-foreground py-12 text-center">
                  No candidates match current filters.
                  {candidates.length === 0 && " Run a scan to populate the list."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterChip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full px-3 h-7 text-xs transition-colors ${
        active ? "bg-primary text-primary-foreground" : "border-input border hover:bg-muted"
      }`}
    >
      {label}
    </button>
  );
}

function Th({
  label, align = "left", width, onClick, active, dir, help,
}: { label?: string; align?: "left" | "right" | "center"; width?: string; onClick?: () => void; active?: boolean; dir?: "asc" | "desc"; help?: string }) {
  return (
    <th
      style={{ width, textAlign: align }}
      onClick={onClick}
      title={help}
      className={`px-2 py-1.5 font-medium ${onClick ? "cursor-pointer select-none hover:text-foreground" : ""} ${active ? "text-foreground" : ""}`}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {onClick && (active
          ? (dir === "asc" ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />)
          : <ChevronsUpDown className="h-3 w-3 opacity-30" />)}
      </span>
    </th>
  );
}

function Row({
  c, inBasket, onToggle,
}: { c: WheelCandidate; inBasket: boolean; onToggle: () => void }) {
  const ann = parseFloat(c.annualized_yield_pct);
  const score = parseFloat(c.score);
  const yieldColor = ann >= 0.20 ? "text-emerald-500" : ann >= 0.10 ? "text-foreground" : "text-muted-foreground";
  const scoreColor = score >= 70 ? "text-emerald-500" : score >= 50 ? "text-amber-500" : "text-muted-foreground";

  return (
    <tr className={`border-t hover:bg-muted/30 ${inBasket ? "bg-primary/5" : ""}`}>
      <td className="px-2 py-1.5 text-center">
        <input type="checkbox" checked={inBasket} onChange={onToggle} className="cursor-pointer" />
      </td>
      <td className="px-2 py-1.5 font-mono font-semibold">
        {c.symbol}
        {c.earnings_before_expiry && (
          <span title={`Earnings ${c.next_earnings_date?.slice(0, 10)} (before expiry)`}>
            <AlertTriangle className="ml-1 inline h-3 w-3 text-amber-500" />
          </span>
        )}
      </td>
      <td className="px-2 py-1.5">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
          c.strategy === "csp" ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-sky-500/15 text-sky-600 dark:text-sky-400"
        }`}>{c.strategy}</span>
      </td>
      <td className="px-2 py-1.5 text-muted-foreground truncate max-w-[8rem]">{c.sector ?? "—"}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{money(c.last_price)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{money(c.strike)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{pct(c.otm_pct)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{c.dte}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{money(c.mid)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{pct(c.premium_yield_pct, 2)}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums font-semibold ${yieldColor}`}>{pct(c.annualized_yield_pct, 1)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{c.delta_approx ?? "—"}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{num(c.open_interest)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">
        {c.bid_ask_spread_pct ? pct(c.bid_ask_spread_pct, 1) : "—"}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums">{money(c.capital_at_risk, 0)}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums font-semibold ${scoreColor}`}>
        {parseFloat(c.score).toFixed(0)}
      </td>
    </tr>
  );
}

function BasketCorrelation({
  symbols, onClear,
}: { symbols: WheelCandidate[]; onClear: () => void }) {
  const [report, setReport] = useState<CorrelationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      const notionals: Record<string, number> = {};
      for (const c of symbols) {
        notionals[c.symbol] = (notionals[c.symbol] ?? 0) + parseFloat(c.capital_at_risk);
      }
      const res = await fetch(`${API_URL}/api/wheel/correlation`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ symbols: Array.from(new Set(symbols.map(s => s.symbol))), notionals }),
      });
      if (!res.ok) throw new Error(await res.text());
      setReport(await res.json());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const total = symbols.reduce((s, c) => s + parseFloat(c.capital_at_risk), 0);

  return (
    <div className="rounded-lg border bg-primary/5 p-3 text-xs space-y-2">
      <div className="flex items-center gap-3">
        <span className="font-semibold">Basket ({symbols.length} legs · {money(total, 0)})</span>
        <button
          onClick={run}
          disabled={loading || symbols.length < 2}
          className="bg-primary text-primary-foreground rounded-md px-3 h-7 text-xs disabled:opacity-50"
        >
          {loading ? "Analyzing…" : "Analyze correlation"}
        </button>
        <button onClick={onClear} className="text-muted-foreground hover:text-foreground ml-auto">
          Clear basket
        </button>
      </div>
      {error && <p className="text-destructive">{error}</p>}
      {report && <CorrelationReportView report={report} />}
    </div>
  );
}

export function CorrelationReportView({ report }: { report: CorrelationReport }) {
  return (
    <div className="space-y-3 pt-2">
      {/* Sector breakdown */}
      <div>
        <div className="text-muted-foreground mb-1 font-medium uppercase tracking-wide text-[10px]">Sector concentration</div>
        <div className="space-y-1">
          {report.sectors.map(s => (
            <div key={s.sector} className="flex items-center gap-2">
              <div className="w-32 text-muted-foreground truncate">{s.sector}</div>
              <div className="flex-1 bg-muted rounded h-2 overflow-hidden">
                <div
                  className={`h-full ${s.pct_of_total > 0.35 ? "bg-destructive" : s.pct_of_total > 0.25 ? "bg-amber-500" : "bg-emerald-500"}`}
                  style={{ width: `${Math.min(100, s.pct_of_total * 100)}%` }}
                />
              </div>
              <div className="w-24 text-right tabular-nums">{pct(s.pct_of_total, 1)} · {money(s.notional, 0)}</div>
              <div className="w-48 text-muted-foreground truncate text-[10px]">{s.symbols.join(", ")}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Top correlated pairs */}
      <div>
        <div className="text-muted-foreground mb-1 font-medium uppercase tracking-wide text-[10px]">Top return correlations (90d)</div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-1">
          {report.pairs.slice(0, 12).map(p => (
            <div key={`${p.a}-${p.b}`} className="flex items-center gap-2">
              <span className="font-mono w-32 truncate">{p.a} ↔ {p.b}</span>
              <div className="flex-1 bg-muted rounded h-1.5 overflow-hidden">
                <div
                  className={`h-full ${Math.abs(p.correlation) >= 0.7 ? "bg-destructive" : Math.abs(p.correlation) >= 0.5 ? "bg-amber-500" : "bg-emerald-500"}`}
                  style={{ width: `${Math.abs(p.correlation) * 100}%` }}
                />
              </div>
              <span className={`tabular-nums w-12 text-right ${Math.abs(p.correlation) >= 0.7 ? "text-destructive font-semibold" : ""}`}>
                {p.correlation.toFixed(2)}
              </span>
            </div>
          ))}
          {report.pairs.length === 0 && (
            <div className="text-muted-foreground italic col-span-2">No price history overlap for these symbols yet — run an EOD sync.</div>
          )}
        </div>
      </div>

      {/* Warnings */}
      {(report.flagged_pairs.length > 0 || report.flagged_sectors.length > 0 || report.single_name_warnings.length > 0) && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 space-y-1">
          {report.flagged_sectors.map(s => (
            <p key={s.sector} className="text-amber-600 dark:text-amber-400">
              ⚠ <b>{pct(s.pct_of_total, 1)}</b> concentrated in <b>{s.sector}</b> ({s.symbols.length} names)
            </p>
          ))}
          {report.flagged_pairs.map(p => (
            <p key={`${p.a}-${p.b}`} className="text-amber-600 dark:text-amber-400">
              ⚠ <b>{p.a}</b> and <b>{p.b}</b> are <b>{p.correlation.toFixed(2)}</b> correlated — not independent diversification
            </p>
          ))}
          {report.single_name_warnings.map(w => (
            <p key={w.symbol} className="text-amber-600 dark:text-amber-400">
              ⚠ <b>{w.symbol}</b> is <b>{pct(w.pct_of_total, 1)}</b> of basket — high single-name concentration
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
