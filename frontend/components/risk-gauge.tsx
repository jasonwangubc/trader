"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Shield } from "lucide-react";
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

interface OpenRiskSummary {
  positions: OpenPosition[];
  total_risk_usd: string;
  total_risk_cad: string;
  total_equity_usd: string;
  total_equity_cad: string;
  risk_pct_usd: string;
  risk_pct_cad: string;
  max_risk_pct: string;
  warning: string | null;
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

  const usdPct   = parseFloat(data.risk_pct_usd) * 100;
  const cadPct   = parseFloat(data.risk_pct_cad) * 100;
  const maxPct   = Math.max(usdPct, cadPct);
  const capPct   = parseFloat(data.max_risk_pct) * 100;
  const barWidth = Math.min(maxPct / capPct * 100, 100);
  const barColor = maxPct >= capPct ? "bg-destructive" : maxPct >= capPct * 0.75 ? "bg-amber-400" : "bg-emerald-500";

  if (data.positions.length === 0) {
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
        <CardDescription className="text-xs">{data.positions.length} active position{data.positions.length !== 1 ? "s" : ""} · max 8% of equity</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.warning && (
          <div className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {data.warning}
          </div>
        )}

        {/* Risk bar */}
        <div className="space-y-1">
          <div className="flex justify-between text-xs">
            <span className="text-muted-foreground">Total open risk</span>
            <span className="font-semibold">{maxPct.toFixed(1)}% / {capPct}% cap</span>
          </div>
          <div className="h-2 rounded-full bg-muted overflow-hidden">
            <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${barWidth}%` }} />
          </div>
        </div>

        {/* Per-currency breakdown */}
        <div className="grid grid-cols-2 gap-2 text-xs">
          {parseFloat(data.total_risk_usd) > 0 && (
            <div>
              <span className="text-muted-foreground">USD at risk</span>
              <div className="font-semibold">{fmtMoney(data.total_risk_usd, "USD")}</div>
              <div className="text-muted-foreground">{fmtPct(data.risk_pct_usd)} of USD equity</div>
            </div>
          )}
          {parseFloat(data.total_risk_cad) > 0 && (
            <div>
              <span className="text-muted-foreground">CAD at risk</span>
              <div className="font-semibold">{fmtMoney(data.total_risk_cad, "CAD")}</div>
              <div className="text-muted-foreground">{fmtPct(data.risk_pct_cad)} of CAD equity</div>
            </div>
          )}
        </div>

        {/* Position list */}
        <div className="space-y-1 border-t pt-2">
          {data.positions.map(p => (
            <div key={p.symbol} className="flex items-center justify-between text-xs">
              <span className="font-mono font-medium">{p.symbol}</span>
              <span className="text-muted-foreground">{p.shares.toLocaleString()} sh · stop ${parseFloat(p.stop_price).toFixed(2)}</span>
              <span className={`tabular-nums ${p.is_paper ? "text-muted-foreground" : "text-destructive"}`}>
                -{fmtMoney(p.open_risk_dollars, p.currency)}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
