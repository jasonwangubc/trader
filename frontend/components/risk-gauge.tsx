"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Shield, Clock, FlaskConical } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

interface OpenPosition {
  symbol: string;
  currency: string;
  shares: number;
  entry_price: string | null;
  stop_price: string;
  open_risk_dollars: string;
  account_type: string;
  is_paper: boolean;
}

interface UnmanagedPosition {
  symbol: string;
  currency: string;
  shares: number;
  market_value: string;
  broker_stop_price?: string | null;
  broker_target_price?: string | null;
  position_id?: string | null;
}

interface PendingOrder {
  symbol: string;
  currency: string;
  side: string;
  order_type: string;
  quantity: number;
  limit_price: string | null;
  stop_price: string | null;
  notional: string;
  est_risk_dollars: string | null;
  matched_ticket_id: string | null;
  is_paper: boolean;
}

interface OpenRiskSummary {
  positions?: OpenPosition[];
  total_risk_usd?: string;
  total_risk_cad?: string;

  // Newer fields (backend ≥ 2026-05-08). Older deployments omit these — the
  // component nullish-coalesces so the UI degrades gracefully.
  paper_positions?: OpenPosition[];
  paper_risk_usd?: string;
  paper_risk_cad?: string;

  pending_orders?: PendingOrder[];
  pending_notional_usd?: string;
  pending_notional_cad?: string;
  pending_risk_usd?: string;
  pending_risk_cad?: string;

  unmanaged_positions?: UnmanagedPosition[];
  unmanaged_value_usd?: string;
  unmanaged_value_cad?: string;

  total_equity_usd?: string;
  total_equity_cad?: string;
  risk_pct_usd?: string;
  risk_pct_cad?: string;
  pending_pct_usd?: string;
  pending_pct_cad?: string;
  max_risk_pct?: string;
  warning?: string | null;
  pending_orders_supported?: boolean;
}

function fmtMoney(val: string, currency: string): string {
  return new Intl.NumberFormat("en-CA", { style: "currency", currency, maximumFractionDigits: 0 }).format(parseFloat(val));
}

function fmtPct(val: string): string {
  return `${(parseFloat(val) * 100).toFixed(1)}%`;
}

export function RiskGauge() {
  const [data, setData] = useState<OpenRiskSummary | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/journal/risk`).then(r => r.json()).then(setData).catch(() => {});
  }, []);

  if (!data) return null;

  // Defensive defaults — older backend versions or partial responses can omit
  // newer fields. Treat missing arrays as empty and missing percentages as 0.
  const positions          = data.positions          ?? [];
  const paperPositions     = data.paper_positions    ?? [];
  const pendingOrders      = data.pending_orders     ?? [];
  const unmanagedPositions = data.unmanaged_positions ?? [];

  const realUsdPct  = parseFloat(data.risk_pct_usd ?? "0") * 100;
  const realCadPct  = parseFloat(data.risk_pct_cad ?? "0") * 100;
  const realMaxPct  = Math.max(realUsdPct, realCadPct);

  const ifFillUsdPct = parseFloat(data.pending_pct_usd ?? "0") * 100;
  const ifFillCadPct = parseFloat(data.pending_pct_cad ?? "0") * 100;
  const ifFillMaxPct = Math.max(ifFillUsdPct, ifFillCadPct);

  const capPct      = parseFloat(data.max_risk_pct ?? "0.08") * 100;
  const realBarW    = Math.min(realMaxPct / capPct * 100, 100);
  const ifFillBarW  = Math.min(ifFillMaxPct / capPct * 100, 100);
  const barColor    = realMaxPct >= capPct ? "bg-destructive"
                    : realMaxPct >= capPct * 0.75 ? "bg-amber-400"
                    : "bg-emerald-500";

  const realPendingCount = pendingOrders.filter(p => !p.is_paper).length;
  const hasPending       = realPendingCount > 0;
  const hasUnmanaged     = unmanagedPositions.length > 0;
  const hasPaper         = paperPositions.length > 0;
  const hasReal          = positions.length > 0;

  if (!hasReal && !hasPending && !hasUnmanaged && !hasPaper) {
    return (
      <Card>
        <CardContent className="pt-4 pb-3 flex items-center gap-2 text-sm text-muted-foreground">
          <Shield className="h-4 w-4" />
          No open positions — risk is zero.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={data.warning ? "border-amber-400 dark:border-amber-600" : ""}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Open risk</CardTitle>
          <Link href="/journal" className="text-primary text-xs hover:underline">Details →</Link>
        </div>
        <CardDescription className="text-xs">
          {positions.length} real · {realPendingCount} pending · {unmanagedPositions.length} unmanaged · max {capPct}% of equity
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.warning && (
          <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {data.warning}
          </div>
        )}

        {/* Real risk bar */}
        <div className="space-y-1.5">
          <div className="flex justify-between items-baseline text-xs">
            <span className="text-muted-foreground">Real account risk</span>
            <span className="font-semibold tabular-nums">{realMaxPct.toFixed(1)}% <span className="text-muted-foreground/60 font-normal">/ {capPct}% cap</span></span>
          </div>
          <div className="h-2 rounded-full bg-muted overflow-hidden relative">
            {/* If-all-fill projection (lighter) */}
            {hasPending && ifFillMaxPct > realMaxPct && (
              <div
                className={`h-full rounded-full ${barColor} opacity-25 absolute inset-y-0 left-0`}
                style={{ width: `${ifFillBarW}%` }}
                title={`If all pending orders fill: ${ifFillMaxPct.toFixed(1)}%`}
              />
            )}
            <div className={`h-full rounded-full transition-all relative ${barColor}`} style={{ width: `${realBarW}%` }} />
          </div>
          {hasPending && ifFillMaxPct > realMaxPct && (
            <div className="text-[11px] text-muted-foreground">
              <Clock className="inline h-3 w-3 mr-1 -mt-0.5" />
              {ifFillMaxPct.toFixed(1)}% if all pending orders fill
            </div>
          )}
        </div>

        {/* Per-currency breakdown */}
        {(parseFloat(data.total_risk_usd ?? "0") > 0 || parseFloat(data.total_risk_cad ?? "0") > 0) && (
          <div className="grid grid-cols-2 gap-2 text-xs pt-1">
            {parseFloat(data.total_risk_usd ?? "0") > 0 && (
              <div>
                <span className="text-muted-foreground">USD at risk</span>
                <div className="font-semibold tabular-nums">{fmtMoney(data.total_risk_usd ?? "0", "USD")}</div>
                <div className="text-muted-foreground">{fmtPct(data.risk_pct_usd ?? "0")} of USD equity</div>
              </div>
            )}
            {parseFloat(data.total_risk_cad ?? "0") > 0 && (
              <div>
                <span className="text-muted-foreground">CAD at risk</span>
                <div className="font-semibold tabular-nums">{fmtMoney(data.total_risk_cad ?? "0", "CAD")}</div>
                <div className="text-muted-foreground">{fmtPct(data.risk_pct_cad ?? "0")} of CAD equity</div>
              </div>
            )}
          </div>
        )}

        {/* Real positions list */}
        {hasReal && (
          <div className="space-y-1 border-t pt-2">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground/60 mb-1">Real positions</div>
            {positions.map(p => (
              <div key={`r-${p.symbol}`} className="flex items-center justify-between text-xs">
                <span className="font-mono font-medium">{p.symbol}</span>
                <span className="text-muted-foreground tabular-nums">{p.shares.toLocaleString()} sh · stop ${parseFloat(p.stop_price).toFixed(2)}</span>
                <span className="tabular-nums text-rose-400">-{fmtMoney(p.open_risk_dollars, p.currency)}</span>
              </div>
            ))}
          </div>
        )}

        {/* Pending orders */}
        {hasPending && (
          <div className="space-y-1 border-t pt-2">
            <div className="text-[11px] uppercase tracking-wide text-muted-foreground/60 mb-1 flex items-center gap-1">
              <Clock className="h-3 w-3" /> Pending orders
            </div>
            {pendingOrders.filter(p => !p.is_paper).map((p, i) => {
              const px = p.limit_price ?? p.stop_price;
              return (
                <div key={`p-${p.symbol}-${i}`} className="flex items-center justify-between text-xs">
                  <span className="font-mono font-medium">{p.symbol}</span>
                  <span className="text-muted-foreground tabular-nums">
                    {p.quantity.toLocaleString()} sh @ {p.order_type.includes("stop") ? "stop" : "limit"} ${px ? parseFloat(px).toFixed(2) : "—"}
                  </span>
                  <span className="tabular-nums text-amber-400/80">
                    {p.est_risk_dollars && parseFloat(p.est_risk_dollars) > 0
                      ? `-${fmtMoney(p.est_risk_dollars, p.currency)}`
                      : <span className="text-muted-foreground/60">no stop</span>}
                  </span>
                </div>
              );
            })}
          </div>
        )}

        {/* Unmanaged warnings — split by whether broker has a sell-stop */}
        {hasUnmanaged && (() => {
          const noStop  = unmanagedPositions.filter(u => !u.broker_stop_price);
          const stopped = unmanagedPositions.filter(u =>  u.broker_stop_price);
          return (
            <div className="border-t pt-2 space-y-1.5">
              {noStop.length > 0 && (
                <Link
                  href="/positions"
                  className="flex items-center justify-between gap-2 text-xs rounded-md bg-rose-500/10 border border-rose-500/30 px-2 py-1.5 hover:bg-rose-500/15 transition-colors"
                >
                  <span className="flex items-center gap-1.5 text-rose-500 font-medium">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {noStop.length} unmanaged · no stop
                  </span>
                  <span className="text-muted-foreground">
                    {noStop.slice(0, 4).map(u => u.symbol).join(", ")}
                    {noStop.length > 4 && ` +${noStop.length - 4}`} →
                  </span>
                </Link>
              )}
              {stopped.length > 0 && (
                <Link
                  href="/positions"
                  className="flex items-center justify-between gap-2 text-xs rounded-md bg-amber-500/10 border border-amber-500/30 px-2 py-1.5 hover:bg-amber-500/15 transition-colors"
                >
                  <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400 font-medium">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    {stopped.length} unmanaged · stop at broker
                  </span>
                  <span className="text-muted-foreground">
                    {stopped.slice(0, 4).map(u => u.symbol).join(", ")}
                    {stopped.length > 4 && ` +${stopped.length - 4}`} → adopt
                  </span>
                </Link>
              )}
            </div>
          );
        })()}

        {/* Paper sandbox */}
        {hasPaper && (
          <div className="border-t pt-2">
            <details className="text-xs">
              <summary className="cursor-pointer flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors">
                <FlaskConical className="h-3.5 w-3.5" />
                Paper sandbox · {paperPositions.length} position{paperPositions.length !== 1 ? "s" : ""}
                {parseFloat(data.paper_risk_usd ?? "0") > 0 && <span className="ml-auto text-muted-foreground/70">{fmtMoney(data.paper_risk_usd ?? "0", "USD")}</span>}
                {parseFloat(data.paper_risk_cad ?? "0") > 0 && <span className={parseFloat(data.paper_risk_usd ?? "0") > 0 ? "ml-1" : "ml-auto"}>{fmtMoney(data.paper_risk_cad ?? "0", "CAD")}</span>}
              </summary>
              <div className="mt-1.5 space-y-1 text-muted-foreground/80">
                {paperPositions.map(p => (
                  <div key={`pp-${p.symbol}`} className="flex items-center justify-between">
                    <span className="font-mono">{p.symbol}</span>
                    <span className="tabular-nums">{p.shares.toLocaleString()} sh · stop ${parseFloat(p.stop_price).toFixed(2)}</span>
                    <span className="tabular-nums">-{fmtMoney(p.open_risk_dollars, p.currency)}</span>
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}

        {data.pending_orders_supported === false && (
          <p className="text-[10px] text-muted-foreground/60 italic border-t pt-2">
            Broker not configured — pending orders not loaded.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
