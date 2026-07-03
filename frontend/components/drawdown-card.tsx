"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

interface DrawdownState {
  peak_equity: string;
  current_equity: string;
  currency: string;
  drawdown_pct: number;
  tier: "ok" | "warn" | "half_risk" | "block";
  risk_multiplier: string;
  has_history: boolean;
  dd_warn: number;
  dd_half_risk: number;
  dd_block: number;
}

const TIER_LABEL: Record<string, string> = {
  ok: "Healthy",
  warn: "Caution",
  half_risk: "Risk halved",
  block: "New tickets blocked",
};

export function DrawdownCard() {
  const [state, setState] = useState<DrawdownState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/accounts/drawdown`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then(setState)
      .catch((e: unknown) => setError(String(e)));
  }, []);

  if (error) return null; // non-critical card — vanish quietly on failure
  if (!state) {
    return <div className="h-28 animate-pulse rounded-lg bg-muted/20" />;
  }

  const ddPct = state.drawdown_pct * 100;
  const tierColor =
    state.tier === "block" ? "text-destructive"
    : state.tier === "half_risk" ? "text-amber-600 dark:text-amber-400"
    : state.tier === "warn" ? "text-amber-600 dark:text-amber-400"
    : "text-emerald-600 dark:text-emerald-400";
  // Gauge: position of current drawdown between 0 and the block threshold.
  const gaugePct = Math.min(100, (state.drawdown_pct / state.dd_block) * 100);
  const gaugeColor =
    state.tier === "block" ? "bg-destructive"
    : state.tier === "half_risk" ? "bg-amber-500"
    : state.tier === "warn" ? "bg-amber-400"
    : "bg-emerald-500";

  return (
    <Card className={state.tier === "block" ? "border-destructive/50" : ""}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          <ShieldAlert className="h-4 w-4" />
          Drawdown circuit breaker
        </CardTitle>
        <CardDescription className="text-xs">
          Equity vs peak in your trading account — warns at −{(state.dd_warn * 100).toFixed(0)}%,
          halves risk at −{(state.dd_half_risk * 100).toFixed(1)}%, blocks new tickets at
          −{(state.dd_block * 100).toFixed(0)}%
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {state.has_history ? (
          <>
            <div className="flex items-baseline justify-between">
              <span className={`text-xl font-semibold tabular-nums ${tierColor}`}>
                −{ddPct.toFixed(1)}%
              </span>
              <span className={`text-sm font-medium ${tierColor}`}>{TIER_LABEL[state.tier]}</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
              <div className={`h-full rounded-full ${gaugeColor}`} style={{ width: `${gaugePct}%` }} />
            </div>
            <p className="text-muted-foreground text-xs">
              {state.currency} {Number(state.current_equity).toLocaleString(undefined, { maximumFractionDigits: 0 })}{" "}
              vs peak {Number(state.peak_equity).toLocaleString(undefined, { maximumFractionDigits: 0 })}.
              History builds from the first sync — seed a pre-app peak via the accounts page if needed.
            </p>
          </>
        ) : (
          <p className="text-muted-foreground text-sm">
            No equity history yet — sync your accounts once and daily snapshots start
            accumulating. The breaker activates from the first recorded peak.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
