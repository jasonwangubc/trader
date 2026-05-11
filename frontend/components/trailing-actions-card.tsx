"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Shield, TrendingUp, Check, X, AlertTriangle, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

interface TrailingAction {
  id: string;
  ticket_id: string;
  symbol: string;
  action_type: "trail_stop" | "scale_out";
  milestone: string;
  old_stop: string | null;
  new_stop: string | null;
  sell_price: string | null;
  sell_shares: number | null;
  leg_label: string | null;
  open_r: string;
  triggered_price: string;
  triggered_at: string;
  status: string;
  is_paper: boolean;
}

export function TrailingActionsCard() {
  const [actions, setActions] = useState<TrailingAction[]>([]);
  const [busy, setBusy] = useState<string | null>(null);  // action id being processed

  const load = () => {
    fetch(`${API_URL}/api/trailing/actions?status=pending`)
      .then(r => r.json())
      .then(setActions)
      .catch(() => {});
  };

  useEffect(() => {
    load();
    // Re-check every 30s — the monitor runs every 15s so this keeps the card fresh
    const h = setInterval(load, 30_000);
    return () => clearInterval(h);
  }, []);

  if (actions.length === 0) return null;

  const confirm = async (id: string) => {
    setBusy(id);
    try {
      await fetch(`${API_URL}/api/trailing/actions/${id}/confirm`, { method: "POST" });
      load();
    } catch {/* ignore */}
    setBusy(null);
  };

  const dismiss = async (id: string) => {
    setBusy(id);
    try {
      await fetch(`${API_URL}/api/trailing/actions/${id}/dismiss`, { method: "POST" });
      load();
    } catch {/* ignore */}
    setBusy(null);
  };

  return (
    <Card className="border-amber-400/60 dark:border-amber-600/60">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            Trailing actions ({actions.length})
          </CardTitle>
          <Link href="/tickets" className="text-primary text-xs hover:underline">All tickets →</Link>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {actions.map(a => (
          <ActionRow
            key={a.id}
            action={a}
            loading={busy === a.id}
            onConfirm={() => confirm(a.id)}
            onDismiss={() => dismiss(a.id)}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function ActionRow({
  action: a,
  loading,
  onConfirm,
  onDismiss,
}: {
  action: TrailingAction;
  loading: boolean;
  onConfirm: () => void;
  onDismiss: () => void;
}) {
  const isScale = a.action_type === "scale_out";
  const openR = parseFloat(a.open_r);
  const label = a.is_paper ? "Paper" : "Live";

  return (
    <div className="rounded-lg border border-border/60 bg-card/60 p-2.5">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-xs font-medium mb-0.5">
            {isScale
              ? <TrendingUp className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
              : <Shield className="h-3.5 w-3.5 text-amber-500 shrink-0" />}
            <span className="font-mono">{a.symbol}</span>
            <span className="text-muted-foreground">·</span>
            <span className={a.is_paper ? "text-muted-foreground" : "text-foreground"}>{label}</span>
            <span className="text-muted-foreground">·</span>
            <span className={`font-semibold ${openR >= 5 ? "text-emerald-400" : openR >= 2 ? "text-amber-400" : "text-foreground"}`}>
              +{openR.toFixed(1)}R
            </span>
          </div>
          <div className="text-[11px] text-muted-foreground">
            {isScale ? (
              <>Scale out <span className="font-mono font-medium">{a.sell_shares} sh</span> @ <span className="font-mono font-medium">${parseFloat(a.sell_price!).toFixed(2)}</span> — {a.leg_label}</>
            ) : (
              <>Trail stop <span className="font-mono">${parseFloat(a.old_stop!).toFixed(2)}</span> → <span className="font-mono font-semibold">${parseFloat(a.new_stop!).toFixed(2)}</span> — {a.milestone}</>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          ) : (
            <>
              <button
                onClick={onConfirm}
                title={isScale ? "Place sell order" : "Update stop at broker"}
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 transition-colors"
              >
                <Check className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={onDismiss}
                title="Dismiss — I'll handle this manually"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-border/60 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
