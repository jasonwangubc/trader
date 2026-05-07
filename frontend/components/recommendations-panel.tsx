"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

interface StopOption {
  method: string;
  price: number;
  distance_pct: number;
  description: string;
}

interface Target {
  label: string;
  r_multiple: number;
  price: number;
  p_20d: number;
  p_40d: number;
}

interface Recs {
  symbol: string;
  entry_price: number;
  stops: StopOption[];
  recommended_stop: StopOption | null;
  targets: Target[];
  atr_14: number;
  base_low: number | null;
  annual_vol_pct: number;
  daily_drift: number;
  expected_value_20d: number;
}

function ProbBar({ p, label }: { p: number; label: string }) {
  const pct = Math.round(p * 100);
  const color = pct >= 60 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground w-8 shrink-0">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums font-medium w-8 text-right">{pct}%</span>
    </div>
  );
}

export function RecommendationsPanel({ symbol }: { symbol: string }) {
  const router = useRouter();
  const [data, setData]       = useState<Recs | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);
  const [selectedStop, setSelectedStop] = useState<StopOption | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_URL}/api/chart/${symbol}/recommendations`)
      .then(r => r.ok ? r.json() : r.json().then((d: any) => Promise.reject(d.detail)))
      .then((d: Recs) => {
        setData(d);
        setSelectedStop(d.recommended_stop);
        setLoading(false);
      })
      .catch((e: unknown) => { setError(String(e)); setLoading(false); });
  }, [symbol]);

  if (loading) return (
    <div className="h-48 rounded-lg bg-muted/20 animate-pulse flex items-center justify-center text-muted-foreground text-sm">
      Computing recommendations…
    </div>
  );
  if (error || !data) return (
    <div className="h-24 rounded-lg bg-muted/20 flex items-center justify-center text-muted-foreground text-sm px-4 text-center">
      {error ?? "No data"}
    </div>
  );

  const stop = selectedStop ?? data.recommended_stop;
  const risk = stop ? data.entry_price - stop.price : 0;

  // Build ticket URL with pre-filled values
  const ticketUrl = stop
    ? `/tickets/new?symbol=${symbol}&trigger=${data.entry_price}&stop=${stop.price}`
    : `/tickets/new?symbol=${symbol}`;

  const driftLabel = data.daily_drift > 0.0005
    ? `+${(data.daily_drift * 252 * 100).toFixed(0)}% ann. drift (bullish momentum)`
    : data.daily_drift < -0.0005
    ? `${(data.daily_drift * 252 * 100).toFixed(0)}% ann. drift (bearish)`
    : "Flat drift";

  return (
    <div className="space-y-5">
      {/* Vol + drift context */}
      <div className="flex flex-wrap gap-4 text-sm">
        <div>
          <span className="text-muted-foreground text-xs">Annual volatility</span>
          <div className="font-semibold">{data.annual_vol_pct.toFixed(1)}%</div>
        </div>
        <div>
          <span className="text-muted-foreground text-xs">14-day ATR</span>
          <div className="font-semibold">${data.atr_14.toFixed(2)}</div>
        </div>
        {data.base_low && (
          <div>
            <span className="text-muted-foreground text-xs">Base low</span>
            <div className="font-semibold">${data.base_low.toFixed(2)}</div>
          </div>
        )}
        <div>
          <span className="text-muted-foreground text-xs">Momentum</span>
          <div className={`font-semibold text-sm ${data.daily_drift > 0.0005 ? "text-emerald-600 dark:text-emerald-400" : data.daily_drift < -0.0005 ? "text-destructive" : ""}`}>
            {driftLabel}
          </div>
        </div>
      </div>

      {/* Stop options */}
      <div>
        <p className="text-sm font-medium mb-2">Stop placement options</p>
        <div className="space-y-2">
          {data.stops.map((s, i) => {
            const isSelected = selectedStop?.method === s.method || (!selectedStop && s.method === data.recommended_stop?.method);
            return (
              <button
                key={i}
                onClick={() => setSelectedStop(s)}
                className={`w-full text-left rounded-lg border p-3 transition-colors ${
                  isSelected
                    ? "border-primary bg-primary/5"
                    : "border-muted hover:border-primary/40"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {isSelected && (
                      <span className="bg-primary text-primary-foreground rounded px-1.5 py-0.5 text-[10px] font-medium">
                        {s.method === data.recommended_stop?.method ? "Recommended" : "Selected"}
                      </span>
                    )}
                    <span className="text-sm font-medium">{s.method}</span>
                  </div>
                  <div className="text-right">
                    <div className="font-mono font-semibold">${s.price.toFixed(2)}</div>
                    <div className="text-muted-foreground text-xs">{s.distance_pct.toFixed(1)}% below</div>
                  </div>
                </div>
                <p className="text-muted-foreground text-xs mt-1 leading-relaxed">{s.description}</p>
              </button>
            );
          })}
        </div>
      </div>

      {/* Targets + probability */}
      {stop && data.targets.length > 0 && (
        <div>
          <p className="text-sm font-medium mb-2">
            Targets (risk = ${risk.toFixed(2)}/share using {stop.method} stop)
          </p>
          <div className="space-y-3">
            {data.targets.map((t, i) => (
              <div key={i} className="rounded-lg border p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold">{t.label}</span>
                  <span className="font-mono font-semibold">${t.price.toFixed(2)}</span>
                </div>
                <div className="space-y-1">
                  <ProbBar p={t.p_20d} label="20d" />
                  <ProbBar p={t.p_40d} label="40d" />
                </div>
                <p className="text-muted-foreground text-xs">
                  {Math.round(t.p_20d * 100)}% chance of reaching ${t.price.toFixed(2)} before stop within 20 trading days,
                  based on {symbol}'s historical volatility ({data.annual_vol_pct.toFixed(0)}% annualised).
                </p>
              </div>
            ))}
          </div>

          {/* Expected value summary */}
          <div className={`mt-3 rounded-lg border px-3 py-2 text-sm ${
            data.expected_value_20d > 0.2 ? "border-emerald-300 bg-emerald-50 dark:bg-emerald-950/20" :
            data.expected_value_20d > 0   ? "border-muted bg-muted/20" :
            "border-amber-300 bg-amber-50 dark:bg-amber-950/20"
          }`}>
            <span className="text-muted-foreground">Estimated edge (T2 trade, 20d): </span>
            <span className={`font-semibold ${data.expected_value_20d > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600"}`}>
              {data.expected_value_20d > 0 ? "+" : ""}{data.expected_value_20d.toFixed(2)}R per trade
            </span>
            <p className="text-muted-foreground text-xs mt-0.5">
              Based on Monte Carlo simulation using {symbol}'s actual daily return distribution.
              Positive = statistically profitable setup at current volatility.
            </p>
          </div>
        </div>
      )}

      {/* Pre-fill ticket button */}
      <div className="border-t pt-3">
        <a
          href={stop ? `/tickets/new?symbol=${symbol}&trigger=${data.entry_price.toFixed(2)}&stop=${stop.price.toFixed(2)}` : `/tickets/new?symbol=${symbol}`}
          className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium hover:bg-primary/90"
        >
          Arm ticket with {stop ? `${stop.method} stop @ $${stop.price.toFixed(2)}` : "these prices"}
        </a>
        <p className="text-muted-foreground text-xs mt-1.5">
          Pre-fills symbol, trigger (current close), and stop. Adjust before arming.
        </p>
      </div>
    </div>
  );
}
