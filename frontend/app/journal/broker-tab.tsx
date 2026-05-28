"use client";

import { useEffect, useState } from "react";
import { Link as LinkIcon, RefreshCw, Pencil, Copy } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";

interface BrokerTrade {
  symbol: string;
  currency: string;
  shares: number;
  avg_entry_price: number;
  avg_exit_price: number;
  entry_date: string;
  exit_date: string;
  hold_days: number;
  realized_pnl: number;
  realized_pnl_pct: number | null;
  r_multiple: number | null;
  setup_type: string;
  is_managed: boolean;
  ticket_id: string | null;
  account_type: string | null;
}

interface SetupBreakdown {
  setup_type: string;
  trades: number;
  wins: number;
  losses: number;
  scratches: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
}

interface MonthBreakdown {
  month: string;
  trades: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
}

interface BrokerJournalSummary {
  total_trades: number;
  wins: number;
  losses: number;
  scratches: number;
  win_rate: number;
  avg_pnl_winner: number;
  avg_pnl_loser: number;
  expectancy_dollars: number;
  profit_factor: number;
  total_realized_pnl_by_ccy: Record<string, number>;
  avg_hold_days: number;
  managed_count: number;
  manual_count: number;
  r_trades_count: number;
  avg_r: number;
  total_r: number;
  by_setup: SetupBreakdown[];
  by_month: MonthBreakdown[];
  equity_curve: Array<{ date: string; symbol: string; pnl: number; cumulative_pnl: number }>;
  trades: BrokerTrade[];
}

interface TradeFromList extends BrokerTrade {
  id: string;
  account_id: string;
  notes: string | null;
  close_reason_tag: string | null;
}

interface TradesListOut {
  trades: TradeFromList[];
  total: number;
}

const SETUP_OPTIONS = ["manual", "VCP", "flat_base", "ep", "cup_handle", "pivot"];

export function BrokerJournalTab() {
  const [data, setData] = useState<BrokerJournalSummary | null>(null);
  const [tradesList, setTradesList] = useState<TradeFromList[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editSetup, setEditSetup] = useState<string>("");
  const [editNotes, setEditNotes] = useState<string>("");

  const load = async () => {
    setLoading(true);
    try {
      const [sum, trades] = await Promise.all([
        fetch("/api/backend/api/journal/broker-summary").then(x => x.json()) as Promise<BrokerJournalSummary>,
        fetch("/api/backend/api/broker-history/trades?limit=500").then(x => x.json()) as Promise<TradesListOut>,
      ]);
      setData(sum);
      setTradesList(trades.trades);
    } catch {
      toast.error("Failed to load broker journal");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const startEdit = (t: TradeFromList) => {
    setEditingId(t.id);
    setEditSetup(t.setup_type);
    setEditNotes(t.notes ?? "");
  };

  const saveEdit = async (id: string) => {
    try {
      const r = await fetch(`/api/backend/api/broker-history/trades/${id}/tag`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ setup_type: editSetup, notes: editNotes }),
      });
      if (!r.ok) throw new Error(await r.text());
      setEditingId(null);
      await load();
      toast.success("Trade tagged");
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="text-muted-foreground py-12 text-center text-sm">
          <RefreshCw className="mx-auto mb-2 h-5 w-5 animate-spin" />
          Loading broker journal…
        </CardContent>
      </Card>
    );
  }

  if (!data || data.total_trades === 0) {
    return (
      <Card>
        <CardContent className="text-muted-foreground py-12 text-center text-sm space-y-2">
          <p>No broker trades synced yet.</p>
          <p className="text-xs">
            Go to <a className="text-primary underline" href="/settings">Settings → Broker trade history</a> and
            click <strong>Sync from Questrade</strong> to backfill your trades.
          </p>
        </CardContent>
      </Card>
    );
  }

  const fmtMoney = (n: number) => `${n >= 0 ? "+" : "−"}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <button
          onClick={() => copyJournalMarkdown(data, tradesList)}
          className="border-input hover:bg-muted inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium"
          title="Copy journal summary as markdown — pastes nicely into chat"
        >
          <Copy className="h-3 w-3" /> Copy journal
        </button>
      </div>

      {/* Summary stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Trades" value={String(data.total_trades)} sub={`${data.managed_count} managed · ${data.manual_count} manual`} />
        <StatCard
          label="Win rate"
          value={`${(data.win_rate * 100).toFixed(1)}%`}
          sub={`${data.wins}W / ${data.losses}L / ${data.scratches}S`}
          color={data.win_rate >= 0.5 ? "green" : data.win_rate >= 0.35 ? "amber" : "red"}
        />
        <StatCard
          label="Total realized"
          value={Object.entries(data.total_realized_pnl_by_ccy).map(([c, v]) => `${fmtMoney(v)} ${c}`).join(" · ") || "—"}
          color={Object.values(data.total_realized_pnl_by_ccy).reduce((s, v) => s + v, 0) >= 0 ? "green" : "red"}
        />
        <StatCard
          label="R-stats (managed)"
          value={data.r_trades_count > 0 ? `${data.avg_r > 0 ? "+" : ""}${data.avg_r.toFixed(2)}R avg` : "—"}
          sub={data.r_trades_count > 0 ? `n=${data.r_trades_count} · total ${data.total_r > 0 ? "+" : ""}${data.total_r.toFixed(1)}R` : "Tag stops on tickets to get R"}
        />
      </div>

      <Card className="bg-muted/30">
        <CardContent className="pt-4 text-xs text-muted-foreground">
          <strong>How to read this:</strong> &quot;Managed&quot; trades are linked to a ticket (with a defined stop, so we can
          compute R). &quot;Manual&quot; trades are ones you placed at Questrade outside the app — tag them with a setup type
          below so behavioral stats stay clean. Cross-currency totals are summed numerically; check the per-currency breakdown for accuracy.
        </CardContent>
      </Card>

      {/* By setup */}
      {data.by_setup.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">By setup type</CardTitle>
            <CardDescription className="text-xs">
              R-multiples shown only for trades with a stop. Tag manual trades to populate.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground border-b text-xs uppercase">
                    <th className="pb-2 text-left">Setup</th>
                    <th className="pb-2 text-right">Trades</th>
                    <th className="pb-2 text-right">W/L/S</th>
                    <th className="pb-2 text-right">Win %</th>
                    <th className="pb-2 text-right">Avg R</th>
                    <th className="pb-2 text-right">Total R</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {data.by_setup.map(s => (
                    <tr key={s.setup_type}>
                      <td className="py-2 font-medium">{s.setup_type}</td>
                      <td className="py-2 text-right tabular-nums">{s.trades}</td>
                      <td className="py-2 text-right tabular-nums text-xs">{s.wins}/{s.losses}/{s.scratches}</td>
                      <td className="py-2 text-right tabular-nums">{(s.win_rate * 100).toFixed(0)}%</td>
                      <td className={`py-2 text-right tabular-nums ${s.avg_r > 0 ? "text-emerald-600 dark:text-emerald-400" : s.avg_r < 0 ? "text-destructive" : ""}`}>
                        {s.avg_r !== 0 ? `${s.avg_r > 0 ? "+" : ""}${s.avg_r.toFixed(2)}R` : "—"}
                      </td>
                      <td className={`py-2 text-right tabular-nums font-medium ${s.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : s.total_r < 0 ? "text-destructive" : ""}`}>
                        {s.total_r !== 0 ? `${s.total_r > 0 ? "+" : ""}${s.total_r.toFixed(1)}R` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Trade log w/ inline tagging */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Trades from broker (newest 500)</CardTitle>
          <CardDescription className="text-xs">
            Click the pencil to tag a manual trade with a setup type.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b uppercase">
                  <th className="pb-2 text-left">Symbol</th>
                  <th className="pb-2 text-left">Acct</th>
                  <th className="pb-2 text-left">Entry</th>
                  <th className="pb-2 text-left">Exit</th>
                  <th className="pb-2 text-right">Shares</th>
                  <th className="pb-2 text-right">Avg in</th>
                  <th className="pb-2 text-right">Avg out</th>
                  <th className="pb-2 text-right">Hold</th>
                  <th className="pb-2 text-right">P&amp;L</th>
                  <th className="pb-2 text-right">%</th>
                  <th className="pb-2 text-right">R</th>
                  <th className="pb-2 text-left">Setup</th>
                  <th className="pb-2 text-center">Mgmt</th>
                  <th className="pb-2 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {tradesList.map(t => (
                  <tr key={t.id}>
                    <td className="py-1 font-mono font-medium">{t.symbol}</td>
                    <td className="py-1 text-muted-foreground">{t.account_type ?? "—"}</td>
                    <td className="py-1 tabular-nums">{t.entry_date.slice(0, 10)}</td>
                    <td className="py-1 tabular-nums">{t.exit_date.slice(0, 10)}</td>
                    <td className="py-1 text-right tabular-nums">{t.shares}</td>
                    <td className="py-1 text-right tabular-nums">{t.avg_entry_price.toFixed(2)}</td>
                    <td className="py-1 text-right tabular-nums">{t.avg_exit_price.toFixed(2)}</td>
                    <td className="py-1 text-right tabular-nums">{t.hold_days}d</td>
                    <td className={`py-1 text-right tabular-nums font-medium ${
                      t.realized_pnl > 0 ? "text-emerald-600 dark:text-emerald-400" : t.realized_pnl < 0 ? "text-destructive" : ""
                    }`}>
                      {fmtMoney(t.realized_pnl)} {t.currency}
                    </td>
                    <td className={`py-1 text-right tabular-nums ${
                      (t.realized_pnl_pct ?? 0) > 0 ? "text-emerald-600 dark:text-emerald-400" : (t.realized_pnl_pct ?? 0) < 0 ? "text-destructive" : ""
                    }`}>
                      {t.realized_pnl_pct !== null ? `${(t.realized_pnl_pct * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td className={`py-1 text-right tabular-nums ${
                      (t.r_multiple ?? 0) > 0 ? "text-emerald-600 dark:text-emerald-400" :
                      (t.r_multiple ?? 0) < 0 ? "text-destructive" : ""
                    }`}>
                      {t.r_multiple !== null ? `${t.r_multiple > 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R` : "—"}
                    </td>
                    <td className="py-1">
                      {editingId === t.id ? (
                        <select
                          value={editSetup}
                          onChange={e => setEditSetup(e.target.value)}
                          className="bg-background border-input h-7 rounded border px-1 text-xs"
                        >
                          {SETUP_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                      ) : (
                        <span className="font-medium">{t.setup_type}</span>
                      )}
                    </td>
                    <td className="py-1 text-center">
                      {t.is_managed ? (
                        <span title="Linked to ticket" className="text-emerald-600 dark:text-emerald-400">
                          <LinkIcon className="inline h-3 w-3" />
                        </span>
                      ) : (
                        <span className="text-muted-foreground">manual</span>
                      )}
                    </td>
                    <td className="py-1 text-right">
                      {editingId === t.id ? (
                        <div className="flex gap-1 justify-end">
                          <button
                            onClick={() => saveEdit(t.id)}
                            className="bg-primary text-primary-foreground rounded px-2 py-0.5 text-xs"
                          >Save</button>
                          <button
                            onClick={() => setEditingId(null)}
                            className="text-muted-foreground rounded px-2 py-0.5 text-xs"
                          >×</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => startEdit(t)}
                          className="text-muted-foreground hover:text-foreground"
                          title="Tag setup type"
                        >
                          <Pencil className="h-3 w-3" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {editingId && (
            <div className="border-input mt-3 rounded-md border p-2">
              <label className="text-xs">Notes (optional)</label>
              <Input
                value={editNotes}
                onChange={e => setEditNotes(e.target.value)}
                placeholder="Why did you take this trade? What did you do well/poorly?"
                className="mt-1 h-8 text-xs"
              />
            </div>
          )}
        </CardContent>
      </Card>

      {/* By month */}
      {data.by_month.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">By month</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-muted-foreground border-b text-xs uppercase">
                    <th className="pb-2 text-left">Month</th>
                    <th className="pb-2 text-right">Trades</th>
                    <th className="pb-2 text-right">Win %</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {data.by_month.map(m => (
                    <tr key={m.month}>
                      <td className="py-2 font-medium tabular-nums">{m.month}</td>
                      <td className="py-2 text-right tabular-nums">{m.trades}</td>
                      <td className="py-2 text-right tabular-nums">{(m.win_rate * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

async function copyJournalMarkdown(
  summary: BrokerJournalSummary,
  trades: TradeFromList[],
): Promise<void> {
  const lines: string[] = [];
  lines.push(`## Journal — from broker (${new Date().toISOString().slice(0, 10)})`);
  lines.push("");
  lines.push(`**Total trades:** ${summary.total_trades.toLocaleString()} (${summary.managed_count} ticket-managed · ${summary.manual_count} manual)`);
  lines.push(`**Batting average:** ${(summary.win_rate * 100).toFixed(1)}% (${summary.wins} wins / ${summary.losses} losses / ${summary.scratches} scratches · profit factor ${summary.profit_factor.toFixed(2)})`);
  const ccyTotals = Object.entries(summary.total_realized_pnl_by_ccy)
    .map(([c, v]) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })} ${c}`)
    .join(" · ");
  lines.push(`**Realized P&L:** ${ccyTotals || "—"}`);
  lines.push(`**Avg win / Avg loss:** ${summary.avg_pnl_winner >= 0 ? "+" : "−"}$${Math.abs(summary.avg_pnl_winner).toLocaleString()} / ${summary.avg_pnl_loser >= 0 ? "+" : "−"}$${Math.abs(summary.avg_pnl_loser).toLocaleString()} · expectancy $${summary.expectancy_dollars.toLocaleString()}/trade`);
  lines.push(`**Avg hold:** ${summary.avg_hold_days.toFixed(1)} days`);
  if (summary.r_trades_count > 0) {
    lines.push(`**R-multiple stats (managed only, n=${summary.r_trades_count}):** avg ${summary.avg_r >= 0 ? "+" : ""}${summary.avg_r.toFixed(2)}R · total ${summary.total_r >= 0 ? "+" : ""}${summary.total_r.toFixed(1)}R`);
  }
  lines.push("");

  if (summary.by_setup.length > 0) {
    lines.push(`### By setup type`);
    lines.push(`| Setup | Trades | W/L/S | Batting | Avg R | Total R |`);
    lines.push(`|-------|-------:|-------|--------:|------:|--------:|`);
    for (const s of summary.by_setup) {
      lines.push(`| ${s.setup_type} | ${s.trades} | ${s.wins}/${s.losses}/${s.scratches} | ${(s.win_rate * 100).toFixed(0)}% | ${s.avg_r >= 0 ? "+" : ""}${s.avg_r.toFixed(2)}R | ${s.total_r >= 0 ? "+" : ""}${s.total_r.toFixed(1)}R |`);
    }
    lines.push("");
  }

  if (summary.by_month.length > 0) {
    lines.push(`### By month`);
    lines.push(`| Month | Trades | Batting |`);
    lines.push(`|-------|-------:|--------:|`);
    for (const m of summary.by_month) {
      lines.push(`| ${m.month} | ${m.trades} | ${(m.win_rate * 100).toFixed(0)}% |`);
    }
    lines.push("");
  }

  const showTrades = trades.slice(0, 100);
  if (showTrades.length > 0) {
    lines.push(`### Recent trades (newest ${showTrades.length})`);
    lines.push(`| Symbol | Acct | Entry | Exit | Shares | Avg in | Avg out | Hold | $ P&L | % | R | Setup | Managed |`);
    lines.push(`|--------|------|-------|------|-------:|-------:|--------:|-----:|------:|---:|---:|-------|--------:|`);
    for (const t of showTrades) {
      const pnlStr = `${t.realized_pnl >= 0 ? "+" : "−"}$${Math.abs(t.realized_pnl).toLocaleString("en-US", { maximumFractionDigits: 0 })} ${t.currency}`;
      const pctStr = t.realized_pnl_pct !== null ? `${(t.realized_pnl_pct * 100).toFixed(1)}%` : "—";
      const rStr = t.r_multiple !== null ? `${t.r_multiple >= 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R` : "—";
      lines.push(`| ${t.symbol} | ${t.account_type ?? "—"} | ${t.entry_date.slice(0, 10)} | ${t.exit_date.slice(0, 10)} | ${t.shares} | ${t.avg_entry_price.toFixed(2)} | ${t.avg_exit_price.toFixed(2)} | ${t.hold_days}d | ${pnlStr} | ${pctStr} | ${rStr} | ${t.setup_type} | ${t.is_managed ? "✓" : ""} |`);
    }
    if (trades.length > 100) lines.push(`\n_…${trades.length - 100} older trades omitted_`);
  }

  try {
    await navigator.clipboard.writeText(lines.join("\n"));
    toast.success(`Copied journal (${lines.length} lines) to clipboard`);
  } catch {
    toast.error("Couldn't access clipboard");
  }
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: "green" | "amber" | "red" }) {
  const cls = color === "green" ? "text-emerald-600 dark:text-emerald-400"
    : color === "red" ? "text-destructive"
    : color === "amber" ? "text-amber-600 dark:text-amber-400" : "";
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-muted-foreground text-xs uppercase tracking-wide">{label}</div>
        <div className={`text-xl font-semibold tabular-nums ${cls}`}>{value}</div>
        {sub && <div className="text-muted-foreground mt-0.5 text-xs">{sub}</div>}
      </CardContent>
    </Card>
  );
}
