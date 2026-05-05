import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type Health, type HealthDb, ApiError } from "@/lib/api";

type Status = { ok: boolean; detail: string };
interface Regime { regime: string; spy_price: number | null; spy_pct_vs_ma200: number | null; message: string }
interface JournalSummary { total_trades: number; win_rate: number; expectancy: number; total_r: number }

async function probe(path: string): Promise<Status> {
  try {
    const res = await api<Health | HealthDb>(path);
    return { ok: res.status === "ok", detail: JSON.stringify(res) };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, detail: `${e.status} ${e.message}` };
    return { ok: false, detail: e instanceof Error ? e.message : String(e) };
  }
}

export default async function DashboardPage() {
  const [app, db] = await Promise.all([probe("/health"), probe("/health/db")]);

  let regime: Regime | null = null;
  let journal: JournalSummary | null = null;
  try {
    [regime, journal] = await Promise.all([
      api<Regime>("/api/regime").catch(() => null),
      api<JournalSummary>("/api/journal/summary").catch(() => null),
    ]);
  } catch {/* ignore */}

  const regimeColor =
    regime?.regime === "bull"    ? "text-emerald-600 dark:text-emerald-400" :
    regime?.regime === "caution" ? "text-amber-600 dark:text-amber-400" :
    regime?.regime === "bear"    ? "text-destructive" : "text-muted-foreground";

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">trader</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Personal trading discipline tool — Minervini SEPA methodology.
        </p>
      </header>

      {/* Regime banner */}
      {regime && (
        <div className={`mb-6 rounded-lg border px-4 py-3 text-sm flex items-center gap-3 ${
          regime.regime === "bear"    ? "border-destructive/40 bg-destructive/5" :
          regime.regime === "caution" ? "border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/20" :
          "border-emerald-200 dark:border-emerald-800 bg-emerald-50 dark:bg-emerald-950/20"
        }`}>
          <span className={`font-bold uppercase text-base ${regimeColor}`}>{regime.regime}</span>
          <span className="text-muted-foreground">{regime.message}</span>
          {regime.spy_price && (
            <span className="ml-auto text-xs text-muted-foreground">
              SPY ${regime.spy_price.toFixed(2)} ({regime.spy_pct_vs_ma200 !== null ? `${regime.spy_pct_vs_ma200 > 0 ? "+" : ""}${regime.spy_pct_vs_ma200.toFixed(1)}%` : "—"} vs 200MA)
            </span>
          )}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatusCard title="Backend" subtitle="FastAPI / health" status={app} />
        <StatusCard title="Database" subtitle="Postgres" status={db} />

        {journal && journal.total_trades > 0 && (
          <Link href="/journal" className="block">
            <Card className="hover:border-primary/50 transition-colors h-full">
              <CardHeader>
                <CardTitle className="text-base">Performance</CardTitle>
                <CardDescription>{journal.total_trades} closed trades</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Win rate</span>
                  <span className="font-semibold">{(journal.win_rate * 100).toFixed(0)}%</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Expectancy</span>
                  <span className={`font-semibold ${journal.expectancy > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
                    {journal.expectancy > 0 ? "+" : ""}{journal.expectancy.toFixed(2)}R
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total R</span>
                  <span className={`font-semibold ${journal.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
                    {journal.total_r > 0 ? "+" : ""}{journal.total_r.toFixed(1)}R
                  </span>
                </div>
              </CardContent>
            </Card>
          </Link>
        )}

        <NavCard href="/tickets/new" title="New ticket" subtitle="Pre-commit: setup, trigger, stop, size" primary />
        <NavCard href="/routine"    title="Routine"    subtitle="Morning checklist + regime check" />
        <NavCard href="/screener"   title="Screener"   subtitle="S&P 500 + NASDAQ 100 + TSX 60 · TT + VCP + EDGAR" />
        <NavCard href="/tickets"    title="Tickets"    subtitle="Armed, triggered, filled" />
        <NavCard href="/options"    title="Options"    subtitle="Covered calls · cash-secured puts" />
        <NavCard href="/positions"  title="Positions"  subtitle="Live holdings + parked cash" />
        <NavCard href="/journal"    title="Journal"    subtitle="Win rate, expectancy, equity curve" />
        <NavCard href="/accounts"   title="Accounts"   subtitle="Questrade balances — TFSA, RRSP, RESP" />
      </div>
    </main>
  );
}

function StatusCard({ title, subtitle, status }: { title: string; subtitle: string; status: Status }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{title}</CardTitle>
          <Badge variant={status.ok ? "default" : "destructive"}>
            {status.ok ? "ok" : "down"}
          </Badge>
        </div>
        <CardDescription className="text-xs">{subtitle}</CardDescription>
      </CardHeader>
    </Card>
  );
}

function NavCard({ href, title, subtitle, primary }: { href: string; title: string; subtitle: string; primary?: boolean }) {
  return (
    <Link href={href} className="block">
      <Card className={`transition-colors hover:border-primary/50 h-full ${primary ? "border-primary/30 bg-primary/5" : ""}`}>
        <CardHeader>
          <CardTitle className={`text-base ${primary ? "text-primary" : ""}`}>{title}</CardTitle>
          <CardDescription className="text-xs">{subtitle}</CardDescription>
        </CardHeader>
      </Card>
    </Link>
  );
}
