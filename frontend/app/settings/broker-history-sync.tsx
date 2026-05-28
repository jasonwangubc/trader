"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { RefreshCw, Download } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface AccountStatus {
  account_id: string;
  questrade_account_id: string;
  account_type: string;
  last_synced_through: string | null;
  last_sync_status: string;
  last_synced_at: string | null;
  last_error: string | null;
  executions_count: number;
  trades_count: number;
}

interface SyncStatus {
  running: boolean;
  user_id: string;
  accounts: AccountStatus[];
  total_executions: number;
  total_trades: number;
  reconciled_trades: number;
}

export function BrokerHistorySync() {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [backfillYears, setBackfillYears] = useState(2);
  const [fullResync, setFullResync] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const poll = async () => {
    try {
      const r = await fetch("/api/backend/api/broker-history/status").then(x => x.json()) as SyncStatus;
      setStatus(r);
      if (r.running) {
        setTimeout(poll, 4000);
      }
    } catch {
      // ignore — backend may be restarting
    }
  };

  useEffect(() => { poll(); }, []);

  const startSync = async () => {
    setSubmitting(true);
    try {
      const r = await fetch("/api/backend/api/broker-history/sync", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ backfill_years: backfillYears, full_resync: fullResync }),
      });
      if (!r.ok) throw new Error(await r.text());
      const ack = await r.json();
      if (ack.status === "already_running") {
        toast.info("A sync is already in progress");
      } else {
        toast.success("Backfill started — this can take several minutes");
      }
      setTimeout(poll, 1000);
    } catch (e) {
      toast.error(`Sync failed: ${(e as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const running = status?.running ?? false;
  const fmtDate = (s: string | null) =>
    s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

  return (
    <div className="space-y-4">
      <div className="text-muted-foreground space-y-1 text-xs">
        <p>
          Pulls every trade activity in your Questrade accounts and reconstructs round-trip trades —
          including trades you placed manually outside the app. Reconciles with existing tickets where dates line up.
        </p>
        <p>
          Initial backfill is chunked into 30-day windows (one network call per chunk). Subsequent
          incremental runs only fetch new activity.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label className="text-xs">Backfill (years)</Label>
          <Input
            type="number" min={1} max={7} step={1}
            value={backfillYears}
            onChange={e => setBackfillYears(parseInt(e.target.value) || 2)}
            className="h-9 w-24 tabular-nums"
          />
        </div>
        <label className="text-foreground flex h-9 cursor-pointer items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={fullResync}
            onChange={e => setFullResync(e.target.checked)}
            className="h-4 w-4"
          />
          Full resync (wipe + rebuild)
        </label>
        <button
          onClick={startSync}
          disabled={submitting || running}
          className="bg-primary text-primary-foreground inline-flex h-9 items-center gap-1.5 rounded-md px-4 text-sm font-medium disabled:opacity-50"
        >
          {running || submitting ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          {running ? "Syncing…" : "Sync from Questrade"}
        </button>
      </div>
      <p className="text-muted-foreground text-xs">
        Tick <strong>Full resync</strong> the first time you sync after this fix — your previous data only
        covered the last 30 days (Questrade&apos;s /executions endpoint has a hard retention cap that we didn&apos;t
        catch). The activities endpoint we now use goes back to account opening.
      </p>

      {status && (
        <div className="bg-muted/30 space-y-3 rounded-md p-3 text-xs">
          <div className="grid grid-cols-3 gap-3">
            <div>
              <div className="text-muted-foreground uppercase tracking-wide">Executions</div>
              <div className="text-lg font-semibold tabular-nums">{status.total_executions.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-muted-foreground uppercase tracking-wide">Trades</div>
              <div className="text-lg font-semibold tabular-nums">{status.total_trades.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-muted-foreground uppercase tracking-wide">Reconciled</div>
              <div className="text-lg font-semibold tabular-nums">{status.reconciled_trades.toLocaleString()}</div>
            </div>
          </div>

          {status.accounts.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-muted-foreground uppercase tracking-wide">Per account</div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground border-b">
                      <th className="py-1 text-left">Account</th>
                      <th className="py-1 text-right">Execs</th>
                      <th className="py-1 text-right">Trades</th>
                      <th className="py-1 text-right">Synced through</th>
                      <th className="py-1 text-right">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {status.accounts.map(a => (
                      <tr key={a.account_id}>
                        <td className="py-1.5 font-mono">{a.account_type} · {a.questrade_account_id}</td>
                        <td className="py-1.5 text-right tabular-nums">{a.executions_count.toLocaleString()}</td>
                        <td className="py-1.5 text-right tabular-nums">{a.trades_count.toLocaleString()}</td>
                        <td className="py-1.5 text-right tabular-nums">{fmtDate(a.last_synced_through)}</td>
                        <td className={`py-1.5 text-right capitalize ${
                          a.last_sync_status === "success" ? "text-emerald-600 dark:text-emerald-400" :
                          a.last_sync_status === "failed"  ? "text-destructive" :
                          a.last_sync_status === "running" ? "text-amber-600 dark:text-amber-400" :
                          "text-muted-foreground"
                        }`}>{a.last_sync_status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {status.accounts.some(a => a.last_error) && (
                <div className="border-destructive/40 bg-destructive/10 text-destructive mt-2 rounded-md border p-2 text-xs">
                  {status.accounts.filter(a => a.last_error).map(a => (
                    <div key={a.account_id}>
                      <strong>{a.account_type}</strong>: {a.last_error}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
