"use client";

import { useEffect, useState } from "react";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { API_URL } from "@/lib/api";

interface QuarterPoint {
  period: string;
  eps: number | null;
  revenue: number | null;
  eps_qoq_pct: number | null;
  revenue_qoq_pct: number | null;
  is_eps_growing: boolean | null;
  is_rev_growing: boolean | null;
}

interface FundamentalsData {
  symbol: string;
  quarters: QuarterPoint[];
  eps_yoy_growth: number | null;
  revenue_yoy_growth: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  roe: number | null;
  gross_margin: number | null;
  operating_margin: number | null;
  net_margin: number | null;
  trailing_eps: number | null;
  forward_eps: number | null;
  acceleration: string;
  acceleration_note: string;
}

function pct(v: number | null, digits = 0) {
  if (v == null) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

function fmt(v: number | null, prefix = "") {
  if (v == null) return "—";
  return `${prefix}${v.toFixed(2)}`;
}

const ACCEL_CONFIG: Record<string, { color: string; bg: string; label: string }> = {
  explosive:    { color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-500", label: "Explosive" },
  accelerating: { color: "text-emerald-600 dark:text-emerald-400", bg: "bg-emerald-400", label: "Accelerating" },
  steady:       { color: "text-amber-600 dark:text-amber-400",    bg: "bg-amber-400",    label: "Steady" },
  decelerating: { color: "text-destructive",                       bg: "bg-destructive",  label: "Decelerating" },
  unknown:      { color: "text-muted-foreground",                  bg: "bg-muted",        label: "Unknown" },
};

export function FundamentalsPanel({ symbol }: { symbol: string }) {
  const [data, setData]       = useState<FundamentalsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_URL}/api/fundamentals/${symbol}`)
      .then(r => r.ok ? r.json() : r.json().then((d: any) => Promise.reject(d.detail)))
      .then((d: FundamentalsData) => { setData(d); setLoading(false); })
      .catch((e: unknown) => { setError(String(e)); setLoading(false); });
  }, [symbol]);

  if (loading) return (
    <div className="animate-pulse rounded-lg bg-muted/20 h-64 flex items-center justify-center text-muted-foreground text-sm">
      Loading fundamentals…
    </div>
  );

  if (error || !data) return (
    <div className="rounded-lg bg-muted/20 h-32 flex items-center justify-center text-muted-foreground text-sm px-4 text-center">
      {error ?? "No fundamental data available"}
    </div>
  );

  const accelConf = ACCEL_CONFIG[data.acceleration] ?? ACCEL_CONFIG.unknown;

  return (
    <div className="space-y-5">
      {/* Acceleration badge + note */}
      <div className="flex items-start gap-3">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold text-white ${accelConf.bg}`}>
          {accelConf.label}
        </span>
        <p className="text-muted-foreground text-sm">{data.acceleration_note}</p>
      </div>

      {/* Key YoY numbers */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiCard
          label="EPS growth (YoY)"
          value={pct(data.eps_yoy_growth, 0)}
          good={data.eps_yoy_growth !== null && data.eps_yoy_growth >= 0.25}
          great={data.eps_yoy_growth !== null && data.eps_yoy_growth >= 0.50}
        />
        <KpiCard
          label="Revenue growth (YoY)"
          value={pct(data.revenue_yoy_growth, 0)}
          good={data.revenue_yoy_growth !== null && data.revenue_yoy_growth >= 0.15}
          great={data.revenue_yoy_growth !== null && data.revenue_yoy_growth >= 0.30}
        />
        <KpiCard
          label="ROE"
          value={pct(data.roe, 0)}
          good={data.roe !== null && data.roe >= 0.17}
          great={data.roe !== null && data.roe >= 0.30}
        />
        <KpiCard
          label="Net margin"
          value={pct(data.net_margin, 1)}
          good={data.net_margin !== null && data.net_margin >= 0.10}
          great={data.net_margin !== null && data.net_margin >= 0.20}
        />
      </div>

      {/* Quarterly EPS bars */}
      {data.quarters.length > 0 && data.quarters.some(q => q.eps !== null) && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <p className="text-sm font-medium">Quarterly EPS</p>
            <span className="text-muted-foreground text-xs">
              Trailing: ${fmt(data.trailing_eps)} · Forward: ${fmt(data.forward_eps)}
            </span>
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={data.quarters} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
              <XAxis dataKey="period" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={v => `$${v.toFixed(2)}`} />
              <Tooltip
                formatter={(v: number, _: string, props: any) => {
                  const pct = props.payload?.eps_qoq_pct;
                  return [`$${v.toFixed(2)}${pct != null ? ` (${pct > 0 ? "+" : ""}${pct.toFixed(1)}% QoQ)` : ""}`, "EPS"];
                }}
              />
              <ReferenceLine y={0} stroke="#6b7280" strokeWidth={0.5} />
              <Bar dataKey="eps" radius={[2, 2, 0, 0]}>
                {data.quarters.map((q, i) => (
                  <Cell
                    key={i}
                    fill={
                      q.is_eps_growing === true  ? "#16a34a" :
                      q.is_eps_growing === false ? "#dc2626" :
                      "#9ca3af"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="text-muted-foreground text-xs mt-1">
            Green = growing QoQ · Minervini target: ≥25% YoY, accelerating each quarter
          </p>
        </div>
      )}

      {/* Quarterly Revenue bars */}
      {data.quarters.length > 0 && data.quarters.some(q => q.revenue !== null) && (
        <div>
          <p className="text-sm font-medium mb-2">Quarterly Revenue ($M)</p>
          <ResponsiveContainer width="100%" height={130}>
            <BarChart data={data.quarters} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
              <XAxis dataKey="period" tick={{ fontSize: 10 }} />
              <YAxis tick={{ fontSize: 10 }} tickFormatter={v => `$${(v/1000).toFixed(0)}B`} />
              <Tooltip
                formatter={(v: number, _: string, props: any) => {
                  const pct = props.payload?.revenue_qoq_pct;
                  const billions = v >= 1000;
                  const label = billions ? `$${(v/1000).toFixed(2)}B` : `$${v.toFixed(0)}M`;
                  return [`${label}${pct != null ? ` (${pct > 0 ? "+" : ""}${pct.toFixed(1)}% QoQ)` : ""}`, "Revenue"];
                }}
              />
              <Bar dataKey="revenue" radius={[2, 2, 0, 0]}>
                {data.quarters.map((q, i) => (
                  <Cell
                    key={i}
                    fill={
                      q.is_rev_growing === true  ? "#2563eb" :
                      q.is_rev_growing === false ? "#dc2626" :
                      "#9ca3af"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Additional ratios */}
      <div className="grid grid-cols-3 gap-3 border-t pt-4 text-sm">
        <div>
          <div className="text-muted-foreground text-xs">Trailing P/E</div>
          <div className="font-semibold">{fmt(data.trailing_pe)}×</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs">Forward P/E</div>
          <div className="font-semibold">{fmt(data.forward_pe)}×</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs">Gross margin</div>
          <div className="font-semibold">{pct(data.gross_margin, 1)}</div>
        </div>
        <div>
          <div className="text-muted-foreground text-xs">Op. margin</div>
          <div className="font-semibold">{pct(data.operating_margin, 1)}</div>
        </div>
      </div>
    </div>
  );
}

function KpiCard({ label, value, good, great }: { label: string; value: string; good: boolean; great: boolean }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className={`text-xl font-bold tabular-nums ${great ? "text-emerald-600 dark:text-emerald-400" : good ? "text-emerald-600/70 dark:text-emerald-500" : "text-foreground"}`}>
        {value}
      </div>
      {(good || great) && (
        <div className="text-[10px] text-emerald-600 dark:text-emerald-400 mt-0.5">
          {great ? "✓ Strong" : "✓ Passing"}
        </div>
      )}
    </div>
  );
}
