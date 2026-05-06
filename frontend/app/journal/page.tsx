import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { type JournalSummary } from "@/lib/screener";
import { fmtMoney } from "@/lib/tickets";

interface Insight {
  category: string;
  severity: string;
  headline: string;
  detail: string;
  data: Record<string, unknown>;
}

const SEVERITY_STYLES: Record<string, string> = {
  bad:  "border-destructive/40 bg-destructive/5",
  warn: "border-amber-400/40 bg-amber-50/50 dark:bg-amber-950/20",
  good: "border-emerald-400/40 bg-emerald-50/50 dark:bg-emerald-950/20",
  info: "border-muted bg-muted/20",
};

const SEVERITY_DOT: Record<string, string> = {
  bad:  "bg-destructive",
  warn: "bg-amber-400",
  good: "bg-emerald-500",
  info: "bg-muted-foreground",
};

export default async function JournalPage() {
  let data: JournalSummary | null = null;
  let insights: Insight[] = [];
  let error: string | null = null;

  try {
    [data, insights] = await Promise.all([
      api<JournalSummary>("/api/journal/summary"),
      api<Insight[]>("/api/journal/coach").catch(() => [] as Insight[]),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Journal</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Closed trade performance — win rate, expectancy, equity curve.
        </p>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">
          {error}
        </div>
      )}

      {data && data.total_trades === 0 && (
        <Card>
          <CardContent className="text-muted-foreground py-16 text-center text-sm">
            No closed trades yet. Close a filled ticket to start your journal.
          </CardContent>
        </Card>
      )}

      {data && data.total_trades > 0 && (
        <div className="space-y-6">
          {/* Summary stats */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Trades" value={String(data.total_trades)} />
            <StatCard
              label="Win rate"
              value={`${(data.win_rate * 100).toFixed(1)}%`}
              sub={`${data.wins}W / ${data.losses}L / ${data.scratches}S`}
              color={data.win_rate >= 0.5 ? "green" : data.win_rate >= 0.35 ? "amber" : "red"}
            />
            <StatCard
              label="Expectancy"
              value={`${data.expectancy > 0 ? "+" : ""}${data.expectancy.toFixed(2)}R`}
              sub={`Avg winner ${data.avg_r_winner.toFixed(2)}R / loser ${data.avg_r_loser.toFixed(2)}R`}
              color={data.expectancy > 0 ? "green" : "red"}
            />
            <StatCard
              label="Profit factor"
              value={data.profit_factor === Infinity ? "∞" : data.profit_factor.toFixed(2)}
              sub={`Total ${data.total_r > 0 ? "+" : ""}${data.total_r.toFixed(1)}R`}
              color={data.profit_factor >= 1.5 ? "green" : data.profit_factor >= 1 ? "amber" : "red"}
            />
          </div>

          {/* Equity curve */}
          {data.equity_curve.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Equity curve (cumulative R)</CardTitle>
                <CardDescription>Each trade adds or subtracts from the running R total.</CardDescription>
              </CardHeader>
              <CardContent>
                <EquityCurveTable curve={data.equity_curve} />
              </CardContent>
            </Card>
          )}

          {/* By setup */}
          {data.by_setup.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">By setup type</CardTitle>
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
                      {data.by_setup.map((s) => (
                        <tr key={s.setup_type}>
                          <td className="py-2 font-medium">{s.setup_type}</td>
                          <td className="py-2 text-right tabular-nums">{s.trades}</td>
                          <td className="py-2 text-right tabular-nums text-xs">{s.wins}/{s.losses}/{s.scratches}</td>
                          <td className="py-2 text-right tabular-nums">{(s.win_rate * 100).toFixed(0)}%</td>
                          <td className={`py-2 text-right tabular-nums ${s.avg_r > 0 ? "text-emerald-600 dark:text-emerald-400" : s.avg_r < 0 ? "text-destructive" : ""}`}>
                            {s.avg_r > 0 ? "+" : ""}{s.avg_r.toFixed(2)}R
                          </td>
                          <td className={`py-2 text-right tabular-nums font-medium ${s.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : s.total_r < 0 ? "text-destructive" : ""}`}>
                            {s.total_r > 0 ? "+" : ""}{s.total_r.toFixed(1)}R
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Behavioral coach */}
          {insights.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Behavioral coach</CardTitle>
                <CardDescription>Patterns found in your closed trades. Sorted by severity.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {insights.map((insight, i) => (
                  <div
                    key={i}
                    className={`rounded-lg border p-3 ${SEVERITY_STYLES[insight.severity] ?? SEVERITY_STYLES.info}`}
                  >
                    <div className="flex items-start gap-2">
                      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[insight.severity] ?? "bg-muted-foreground"}`} />
                      <div>
                        <p className="text-sm font-medium">{insight.headline}</p>
                        <p className="text-muted-foreground mt-0.5 text-xs">{insight.detail}</p>
                      </div>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

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
                        <th className="pb-2 text-right">Avg R</th>
                        <th className="pb-2 text-right">Total R</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {data.by_month.map((m) => (
                        <tr key={m.month}>
                          <td className="py-2 font-medium tabular-nums">{m.month}</td>
                          <td className="py-2 text-right tabular-nums">{m.trades}</td>
                          <td className="py-2 text-right tabular-nums">{(m.win_rate * 100).toFixed(0)}%</td>
                          <td className={`py-2 text-right tabular-nums ${m.avg_r > 0 ? "text-emerald-600 dark:text-emerald-400" : m.avg_r < 0 ? "text-destructive" : ""}`}>
                            {m.avg_r > 0 ? "+" : ""}{m.avg_r.toFixed(2)}R
                          </td>
                          <td className={`py-2 text-right tabular-nums font-medium ${m.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : m.total_r < 0 ? "text-destructive" : ""}`}>
                            {m.total_r > 0 ? "+" : ""}{m.total_r.toFixed(1)}R
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </main>
  );
}

function StatCard({
  label, value, sub, color,
}: {
  label: string; value: string; sub?: string; color?: "green" | "amber" | "red";
}) {
  const cls = color === "green" ? "text-emerald-600 dark:text-emerald-400"
    : color === "red" ? "text-destructive"
    : color === "amber" ? "text-amber-600 dark:text-amber-400"
    : "";
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-muted-foreground text-xs uppercase tracking-wide">{label}</div>
        <div className={`text-2xl font-semibold tabular-nums ${cls}`}>{value}</div>
        {sub && <div className="text-muted-foreground mt-0.5 text-xs">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function EquityCurveTable({ curve }: { curve: Array<{ date: string | null; symbol: string; r: number; cumulative_r: number }> }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted-foreground border-b text-xs uppercase">
            <th className="pb-2 text-left">Date</th>
            <th className="pb-2 text-left">Symbol</th>
            <th className="pb-2 text-right">R</th>
            <th className="pb-2 text-right">Cumulative R</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {curve.map((pt, i) => (
            <tr key={i}>
              <td className="py-1.5 tabular-nums text-xs">{pt.date ?? "—"}</td>
              <td className="py-1.5 font-mono text-xs">{pt.symbol}</td>
              <td className={`py-1.5 text-right tabular-nums ${pt.r > 0 ? "text-emerald-600 dark:text-emerald-400" : pt.r < 0 ? "text-destructive" : ""}`}>
                {pt.r > 0 ? "+" : ""}{pt.r.toFixed(2)}R
              </td>
              <td className={`py-1.5 text-right tabular-nums font-medium ${pt.cumulative_r > 0 ? "text-emerald-600 dark:text-emerald-400" : pt.cumulative_r < 0 ? "text-destructive" : ""}`}>
                {pt.cumulative_r > 0 ? "+" : ""}{pt.cumulative_r.toFixed(2)}R
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
