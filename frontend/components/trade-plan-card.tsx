"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

interface Odds {
  available: boolean;
  reason: string | null;
  cohort: string;
  widened: boolean;
  n_setups: number;
  n_triggered: number;
  trigger_rate: number;
  target_pct: number;
  stop_pct: number;
  time_pct: number;
  avg_r: number;
  time_avg_r: number;
  stop_atr: number;
  target_r: number;
  time_stop_days: number;
  scan_date: string | null;
  caveats: string[];
}

interface Sizing {
  shares: number;
  risk_amount: number;
  risk_pct: number;
  equity_basis: number;
  equity_currency: string;
  warnings: string[];
}

interface TradePlan {
  symbol: string;
  pattern_type: string | null;
  pattern_quality: number | null;
  buyability: string | null;
  tier: string;
  pivot: number | null;
  stop: number | null;
  stop_method: string | null;
  target: number | null;
  target_r: number | null;
  last_close: number | null;
  atr14: number | null;
  sizing: Sizing | null;
  odds: Odds;
}

const STOP_METHOD_LABEL: Record<string, string> = {
  base_low: "0.5% below base low (setup invalidation)",
  atr: "1.5× ATR below trigger (volatility-adjusted)",
  pct: "7% below trigger (max-loss rule)",
};

function Cell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div title={hint}>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="font-mono font-semibold">{value}</div>
    </div>
  );
}

function OddsBar({ odds }: { odds: Odds }) {
  const t = Math.round(odds.target_pct * 100);
  const s = Math.round(odds.stop_pct * 100);
  const m = Math.max(0, 100 - t - s);
  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full">
        <div className="bg-emerald-500" style={{ width: `${t}%` }} title={`Hit target first: ${t}%`} />
        <div className="bg-slate-400" style={{ width: `${m}%` }} title={`Timed out: ${m}%`} />
        <div className="bg-rose-400" style={{ width: `${s}%` }} title={`Stopped out: ${s}%`} />
      </div>
      <div className="mt-1.5 flex justify-between text-xs">
        <span className="text-emerald-600 dark:text-emerald-400 font-medium">{t}% hit target</span>
        <span className="text-muted-foreground">{m}% timed out (avg {odds.time_avg_r >= 0 ? "+" : ""}{odds.time_avg_r.toFixed(2)}R)</span>
        <span className="text-rose-500 font-medium">{s}% stopped</span>
      </div>
    </div>
  );
}

export function TradePlanCard({ symbol }: { symbol: string }) {
  const [plan, setPlan] = useState<TradePlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_URL}/api/chart/${symbol}/trade-plan`)
      .then((r) => (r.ok ? r.json() : r.json().then((d: { detail?: string }) => Promise.reject(d.detail ?? r.statusText))))
      .then((d: TradePlan) => { setPlan(d); setLoading(false); })
      .catch((e: unknown) => { setError(String(e)); setLoading(false); });
  }, [symbol]);

  if (loading) {
    return (
      <div className="h-40 animate-pulse rounded-lg bg-muted/20 flex items-center justify-center text-muted-foreground text-sm">
        Building trade plan… (replaying similar historical setups)
      </div>
    );
  }
  if (error || !plan) {
    return (
      <div className="rounded-lg bg-muted/20 px-4 py-6 text-center text-muted-foreground text-sm">
        {error ?? "No trade plan available"}
      </div>
    );
  }

  const risk = plan.pivot !== null && plan.stop !== null ? plan.pivot - plan.stop : null;
  const odds = plan.odds;
  const ticketUrl =
    plan.pivot !== null && plan.stop !== null
      ? `/tickets/new?symbol=${plan.symbol}&trigger=${plan.pivot.toFixed(2)}&stop=${plan.stop.toFixed(2)}${plan.target ? `&target=${plan.target.toFixed(2)}` : ""}`
      : `/tickets/new?symbol=${plan.symbol}`;

  return (
    <div className="space-y-4">
      {/* Setup context */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {plan.tier && (
          <span
            className="rounded bg-primary/10 px-1.5 py-0.5 font-semibold text-primary"
            title="Setup tier from screener rules: S = strongest momentum patterns at pivot, A = quality base at pivot, B = quality base still forming"
          >
            Tier {plan.tier}
          </span>
        )}
        {plan.pattern_type && (
          <span className="rounded bg-muted px-1.5 py-0.5" title="Detected chart pattern">
            {plan.pattern_type.replace(/_/g, " ")}
            {plan.pattern_quality !== null && ` · q ${plan.pattern_quality.toFixed(2)}`}
          </span>
        )}
        {plan.buyability && (
          <span className="rounded bg-muted px-1.5 py-0.5" title="Where price sits relative to the buy point">
            {plan.buyability.replace(/_/g, " ")}
          </span>
        )}
      </div>

      {/* The plan */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Cell
          label="Buy trigger (pivot)"
          value={plan.pivot !== null ? `$${plan.pivot.toFixed(2)}` : "—"}
          hint="Breakout buy point — the high of the most recent contraction in the base"
        />
        <Cell
          label="Stop loss"
          value={plan.stop !== null ? `$${plan.stop.toFixed(2)}` : "—"}
          hint={plan.stop_method ? STOP_METHOD_LABEL[plan.stop_method] ?? plan.stop_method : undefined}
        />
        <Cell
          label={`First target${plan.target_r ? ` (${plan.target_r.toFixed(1)}R)` : ""}`}
          value={plan.target !== null ? `$${plan.target.toFixed(2)}` : "—"}
          hint="R = risk unit (trigger − stop). A 3R target pays 3× what the stop loses."
        />
        <Cell
          label="Risk / share"
          value={risk !== null ? `$${risk.toFixed(2)}` : "—"}
          hint="Trigger minus stop — the amount lost per share if stopped out"
        />
      </div>

      {/* Sizing from active account */}
      {plan.sizing && (
        <div className="rounded-lg border bg-muted/20 px-3 py-2 text-sm">
          <span className="font-semibold">{plan.sizing.shares} shares</span>
          <span className="text-muted-foreground">
            {" "}· risks {plan.sizing.equity_currency} ${plan.sizing.risk_amount.toFixed(0)}{" "}
            ({(plan.sizing.risk_pct * 100).toFixed(2)}% of your {plan.sizing.equity_currency}{" "}
            ${Math.round(plan.sizing.equity_basis).toLocaleString()} trading account)
          </span>
          {plan.sizing.warnings.length > 0 && (
            <p className="text-amber-600 dark:text-amber-400 text-xs mt-1">{plan.sizing.warnings.join(" ")}</p>
          )}
        </div>
      )}

      {/* Empirical odds */}
      <div className="rounded-lg border p-3">
        <p className="text-sm font-medium mb-2" title="Computed by replaying this plan's stop/target over similar historical setups from the two-year backtest signal cache">
          Historical odds of this plan
        </p>
        {odds.available ? (
          <div className="space-y-2">
            <OddsBar odds={odds} />
            <p className="text-muted-foreground text-xs leading-relaxed">
              Of <span className="font-semibold text-foreground">{odds.n_setups}</span> similar setups
              ({odds.cohort}{odds.widened ? " — widened for sample size" : ""}) over ~2 years,{" "}
              {Math.round(odds.trigger_rate * 100)}% broke out; of those,{" "}
              {Math.round(odds.target_pct * 100)}% reached +{odds.target_r.toFixed(1)}R before the stop.
              Average outcome {odds.avg_r >= 0 ? "+" : ""}{odds.avg_r.toFixed(2)}R per triggered trade.
              {odds.scan_date && ` Signal scan: ${odds.scan_date}.`}
            </p>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Limitations of this estimate</summary>
              <ul className="mt-1 list-disc pl-4 space-y-0.5">
                {odds.caveats.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </details>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">{odds.reason}</p>
        )}
      </div>

      {/* Create ticket */}
      <div>
        <a
          href={ticketUrl}
          className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium hover:bg-primary/90"
        >
          Create ticket from this plan
        </a>
        <p className="text-muted-foreground text-xs mt-1.5">
          Pre-fills trigger, stop, and target. Sizing and guardrails run on the ticket form.
        </p>
      </div>
    </div>
  );
}
