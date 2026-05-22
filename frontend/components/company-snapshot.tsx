"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { API_URL } from "@/lib/api";

interface AnnualPoint {
  year: number;
  eps: number | null;
  revenue: number | null;
  net_income: number | null;
  eps_yoy_pct: number | null;
  revenue_yoy_pct: number | null;
}

interface Snapshot {
  name: string | null;
  exchange: string | null;
  industry: string | null;
  sector: string | null;
  country: string | null;
  website: string | null;
  description: string | null;
  market_cap: number | null;
  enterprise_value: number | null;
  shares_outstanding: number | null;
  float_shares: number | null;
  avg_volume_10d: number | null;
  beta: number | null;
  dividend_yield: number | null;   // percent units, e.g. 2.61 = 2.61%
  dividend_rate: number | null;
  payout_ratio: number | null;
  price: number | null;
  fifty_two_week_high: number | null;
  fifty_two_week_low: number | null;
  ex_dividend_date: string | null;
}

interface Bundle {
  snapshot: Snapshot | null;
  annual: AnnualPoint[];
  trailing_pe: number | null;
  forward_pe: number | null;
  peg_ratio: number | null;
  price_to_book: number | null;
  price_to_sales: number | null;
  roa: number | null;
  debt_to_equity: number | null;
}

function fmtMarketCap(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9)  return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6)  return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

function fmtShares(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toLocaleString();
}

function fmtVolume(v: number | null): string {
  if (v == null) return "—";
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toLocaleString();
}

function fmt(v: number | null, digits = 2, suffix = ""): string {
  if (v == null) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}

const SUMMARY_CLAMP = 320;

export function CompanySnapshot({ symbol }: { symbol: string }) {
  const [data, setData] = useState<Bundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_URL}/api/fundamentals/${symbol}`)
      .then(r => r.ok ? r.json() : r.json().then((d: { detail?: string }) => Promise.reject(d.detail)))
      .then((d: Bundle) => { setData(d); setLoading(false); })
      .catch((e: unknown) => { setError(String(e)); setLoading(false); });
  }, [symbol]);

  if (loading) {
    return (
      <div className="animate-pulse rounded-lg bg-muted/20 h-32 flex items-center justify-center text-muted-foreground text-sm">
        Loading company info…
      </div>
    );
  }

  if (error || !data?.snapshot) {
    return null; // soft-fail; chart page works without snapshot
  }

  const s = data.snapshot;
  const desc = s.description ?? "";
  const isLong = desc.length > SUMMARY_CLAMP;
  const displayDesc = !isLong || showAll ? desc : desc.slice(0, SUMMARY_CLAMP).trimEnd() + "…";

  // 52w range position 0-1
  const range52: number | null = (s.price != null && s.fifty_two_week_low != null && s.fifty_two_week_high != null && s.fifty_two_week_high > s.fifty_two_week_low)
    ? Math.max(0, Math.min(1, (s.price - s.fifty_two_week_low) / (s.fifty_two_week_high - s.fifty_two_week_low)))
    : null;

  return (
    <div className="space-y-4">
      {/* Header: name + industry + website */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        {s.name && <h2 className="text-lg font-semibold">{s.name}</h2>}
        {(s.industry || s.sector) && (
          <span className="text-muted-foreground text-xs">
            {[s.industry, s.sector].filter(Boolean).join(" · ")}
          </span>
        )}
        {s.country && <span className="text-muted-foreground text-xs">· {s.country}</span>}
        {s.website && (
          <a
            href={s.website}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary inline-flex items-center gap-1 text-xs hover:underline"
          >
            {s.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>

      {/* Description */}
      {desc && (
        <div className="text-sm text-muted-foreground leading-relaxed">
          <p>{displayDesc}</p>
          {isLong && (
            <button
              onClick={() => setShowAll(v => !v)}
              className="text-primary mt-1 inline-flex items-center gap-1 text-xs hover:underline"
            >
              {showAll ? <>Show less <ChevronUp className="h-3 w-3" /></> : <>Read more <ChevronDown className="h-3 w-3" /></>}
            </button>
          )}
        </div>
      )}

      {/* Key stats grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-x-4 gap-y-2 text-xs border-y py-3">
        <Stat label="Market cap"      value={fmtMarketCap(s.market_cap)} />
        <Stat label="Enterprise value" value={fmtMarketCap(s.enterprise_value)} />
        <Stat label="P/E (TTM)"        value={fmt(data.trailing_pe, 1, "×")} />
        <Stat label="P/E (fwd)"        value={fmt(data.forward_pe, 1, "×")} />
        <Stat label="PEG"              value={fmt(data.peg_ratio, 2)} />
        <Stat label="P/B"              value={fmt(data.price_to_book, 2)} />
        <Stat label="P/S"              value={fmt(data.price_to_sales, 2)} />
        <Stat label="Beta"             value={fmt(s.beta, 2)} />
        <Stat label="Div yield"        value={s.dividend_yield != null ? `${s.dividend_yield.toFixed(2)}%` : "—"} />
        <Stat label="Div / share"      value={s.dividend_rate != null ? `$${s.dividend_rate.toFixed(2)}` : "—"} />
        <Stat label="Shares out"       value={fmtShares(s.shares_outstanding)} />
        <Stat label="Avg vol (10d)"    value={fmtVolume(s.avg_volume_10d)} />
        <Stat label="ROA"              value={data.roa != null ? `${(data.roa * 100).toFixed(1)}%` : "—"} />
        <Stat label="Debt / equity"    value={data.debt_to_equity != null ? `${(data.debt_to_equity / 100).toFixed(2)}` : "—"} />
        <Stat label="Float"            value={fmtShares(s.float_shares)} />
        <Stat label="Ex-div date"      value={s.ex_dividend_date ?? "—"} />
      </div>

      {/* 52-week range visualization */}
      {range52 !== null && s.price != null && s.fifty_two_week_low != null && s.fifty_two_week_high != null && (
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">52-week range</span>
            <span className="tabular-nums">
              ${s.fifty_two_week_low.toFixed(2)} – ${s.fifty_two_week_high.toFixed(2)}
            </span>
          </div>
          <div className="relative h-2 rounded-full bg-muted">
            <div
              className="absolute top-1/2 -translate-y-1/2 h-3 w-0.5 bg-primary"
              style={{ left: `calc(${range52 * 100}% - 1px)` }}
            />
          </div>
          <div className="text-muted-foreground text-[10px] flex justify-between">
            <span>{(range52 * 100).toFixed(0)}% of range</span>
            <span className="tabular-nums">current ${s.price.toFixed(2)}</span>
          </div>
        </div>
      )}

      {/* Annual EPS bar chart — last 5 years */}
      {data.annual.length > 0 && data.annual.some(a => a.eps !== null) && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-medium">Annual EPS — last {data.annual.length} year{data.annual.length > 1 ? "s" : ""}</p>
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={data.annual} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <XAxis dataKey="year" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={v => `$${v.toFixed(2)}`} />
              <Tooltip
                contentStyle={{ background: "rgb(20 20 20)", border: "1px solid rgb(50 50 50)", fontSize: 12 }}
                formatter={(v: unknown, _name: unknown, props: { payload?: AnnualPoint }) => {
                  const f = typeof v === "number" ? v : 0;
                  const yoy = props.payload?.eps_yoy_pct;
                  return [`$${f.toFixed(2)}${yoy != null ? ` (${yoy > 0 ? "+" : ""}${yoy.toFixed(1)}% YoY)` : ""}`, "EPS"];
                }}
              />
              <ReferenceLine y={0} stroke="#6b7280" strokeWidth={0.5} />
              <Bar dataKey="eps" radius={[2, 2, 0, 0]}>
                {data.annual.map((a, i) => (
                  <Cell
                    key={i}
                    fill={
                      a.eps_yoy_pct == null ? "#9ca3af" :
                      a.eps_yoy_pct > 0     ? "#16a34a" :
                      "#dc2626"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Annual revenue bar chart */}
      {data.annual.length > 0 && data.annual.some(a => a.revenue !== null) && (
        <div>
          <p className="text-sm font-medium mb-2">Annual Revenue ($M)</p>
          <ResponsiveContainer width="100%" height={130}>
            <BarChart data={data.annual} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
              <XAxis dataKey="year" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={v => v >= 1000 ? `$${(v/1000).toFixed(0)}B` : `$${v.toFixed(0)}M`} />
              <Tooltip
                contentStyle={{ background: "rgb(20 20 20)", border: "1px solid rgb(50 50 50)", fontSize: 12 }}
                formatter={(v: unknown, _name: unknown, props: { payload?: AnnualPoint }) => {
                  const f = typeof v === "number" ? v : 0;
                  const yoy = props.payload?.revenue_yoy_pct;
                  const label = f >= 1000 ? `$${(f/1000).toFixed(2)}B` : `$${f.toFixed(0)}M`;
                  return [`${label}${yoy != null ? ` (${yoy > 0 ? "+" : ""}${yoy.toFixed(1)}% YoY)` : ""}`, "Revenue"];
                }}
              />
              <Bar dataKey="revenue" radius={[2, 2, 0, 0]}>
                {data.annual.map((a, i) => (
                  <Cell
                    key={i}
                    fill={
                      a.revenue_yoy_pct == null ? "#9ca3af" :
                      a.revenue_yoy_pct > 0     ? "#2563eb" :
                      "#dc2626"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-muted-foreground text-[10px] uppercase tracking-wide">{label}</div>
      <div className="font-semibold tabular-nums">{value}</div>
    </div>
  );
}
