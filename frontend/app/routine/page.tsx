import { CheckCircle, XCircle, AlertTriangle, Clock } from "lucide-react";
import { RiskGauge } from "@/components/risk-gauge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api, ApiError } from "@/lib/api";
import { fmtMoney, fmtPct } from "@/lib/tickets";
import Link from "next/link";

export const metadata = { title: 'Daily Routine' };


interface Regime {
  regime: string;
  spy_price: number | null;
  spy_ma200: number | null;
  spy_pct_vs_ma200: number | null;
  xiu_price: number | null;
  xiu_ma200: number | null;
  xiu_pct_vs_ma200: number | null;
  distribution_days: number;
  distribution_status: string;
  message: string;
}

interface MonitorStatus {
  running: boolean;
  armed_tickets: number;
  last_tick_at: string | null;
  kill_switch: boolean;
  market_open: boolean;
}

interface StreakState {
  consecutive_wins: number;
  consecutive_losses: number;
  multiplier: string;
  cooldown_active: boolean;
  last_outcome: string | null;
}

const REGIME_CONFIG = {
  bull:    { color: "text-emerald-600 dark:text-emerald-400", label: "BULL",    Icon: CheckCircle,    bg: "bg-emerald-50 dark:bg-emerald-950/30" },
  caution: { color: "text-amber-600 dark:text-amber-400",    label: "CAUTION", Icon: AlertTriangle,  bg: "bg-amber-50 dark:bg-amber-950/30"    },
  bear:    { color: "text-destructive",                       label: "BEAR",    Icon: XCircle,        bg: "bg-destructive/5"                    },
};

export default async function RoutinePage() {
  let regime: Regime | null = null;
  let monitor: MonitorStatus | null = null;
  let error: string | null = null;

  try {
    [regime, monitor] = await Promise.all([
      api<Regime>("/api/regime"),
      api<MonitorStatus>("/api/monitor/status"),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  const now = new Date();
  const etTime = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "numeric", minute: "2-digit", hour12: true
  }).format(now);

  const rc = regime ? (REGIME_CONFIG[regime.regime as keyof typeof REGIME_CONFIG] ?? REGIME_CONFIG.caution) : null;

  return (
    <main className="container mx-auto max-w-3xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Daily routine</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Morning checklist — {now.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })} · {etTime} ET
        </p>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">
          {error}
        </div>
      )}

      <div className="space-y-4">
        {/* 1. Market regime */}
        {rc && regime && (
          <Card className={rc.bg}>
            <CardHeader>
              <div className="flex items-center gap-3">
                <rc.Icon className={`h-5 w-5 ${rc.color}`} />
                <div>
                  <CardTitle className={`text-base ${rc.color}`}>
                    Market regime: {rc.label}
                  </CardTitle>
                  <CardDescription className="text-xs mt-0.5">{regime.message}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4 text-sm">
              {regime.spy_price && (
                <div>
                  <div className="text-muted-foreground text-xs mb-1">SPY</div>
                  <div className="font-semibold">${regime.spy_price.toFixed(2)}</div>
                  <div className="text-muted-foreground text-xs">
                    200 SMA ${regime.spy_ma200?.toFixed(2)} · {regime.spy_pct_vs_ma200 !== null ? `${regime.spy_pct_vs_ma200 > 0 ? "+" : ""}${regime.spy_pct_vs_ma200.toFixed(1)}%` : "—"}
                  </div>
                </div>
              )}
              {regime.xiu_price && (
                <div>
                  <div className="text-muted-foreground text-xs mb-1">XIU (TSX)</div>
                  <div className="font-semibold">${regime.xiu_price.toFixed(2)}</div>
                  <div className="text-muted-foreground text-xs">
                    200 SMA ${regime.xiu_ma200?.toFixed(2)} · {regime.xiu_pct_vs_ma200 !== null ? `${regime.xiu_pct_vs_ma200 > 0 ? "+" : ""}${regime.xiu_pct_vs_ma200.toFixed(1)}%` : "—"}
                  </div>
                </div>
              )}
              {/* Distribution days */}
              <div className="col-span-2 border-t pt-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Distribution days (last 25 sessions)</span>
                  <span className={`font-semibold ${
                    regime.distribution_status === "heavy"    ? "text-destructive" :
                    regime.distribution_status === "elevated" ? "text-amber-600 dark:text-amber-400" :
                    "text-emerald-600 dark:text-emerald-400"
                  }`}>
                    {regime.distribution_days} — {regime.distribution_status}
                  </span>
                </div>
                <p className="text-muted-foreground text-xs mt-1">
                  Counts sessions where SPY closed down ≥0.2% on rising volume (institutional selling signal).
                  5+ days = uptrend under pressure.
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {!regime && (
          <Card>
            <CardContent className="py-6 text-sm text-muted-foreground text-center">
              No regime data — run a screener sync to download SPY and XIU data.
              <div className="mt-2">
                <Link href="/screener/sync?mode=manual" className="text-primary hover:underline text-xs">
                  Sync EOD data →
                </Link>
              </div>
            </CardContent>
          </Card>
        )}

        {/* 2. Monitor status */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Breakout monitor</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {monitor ? (
              <>
                <ChecklistRow
                  ok={!monitor.kill_switch}
                  label={monitor.kill_switch ? "Kill switch is ON — monitor halted" : "Kill switch off — monitor active"}
                />
                <ChecklistRow
                  ok={monitor.armed_tickets > 0}
                  label={`${monitor.armed_tickets} armed ticket${monitor.armed_tickets !== 1 ? "s" : ""} being watched`}
                  neutral={monitor.armed_tickets === 0}
                />
                <ChecklistRow
                  ok={monitor.market_open}
                  label={monitor.market_open ? "Market is open — polling every 15s" : "Market closed — monitor will activate at 9:30 ET"}
                  neutral={!monitor.market_open}
                />
                {monitor.last_tick_at && (
                  <p className="text-muted-foreground text-xs pt-1">
                    Last tick: {new Date(monitor.last_tick_at).toLocaleTimeString()}
                  </p>
                )}
              </>
            ) : (
              <p className="text-muted-foreground text-xs">Monitor data unavailable</p>
            )}
          </CardContent>
        </Card>

        {/* 3. Pre-market actions */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Pre-market checklist</CardTitle>
            <CardDescription>Complete before placing any orders.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <ChecklistRow ok={true} label="Review overnight news for held positions" neutral />
            <ChecklistRow ok={true} label="Check earnings calendar — no trading on earnings day" neutral />
            <ChecklistRow ok={true} label="Confirm all stops are in place" neutral />
            <ChecklistRow ok={true} label="Review armed tickets — are they still valid setups?" neutral />
            {regime?.regime === "bear" && (
              <div className="mt-2 rounded-md border border-destructive/50 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                Bear market: do not create new tickets. Protect capital.
              </div>
            )}
          </CardContent>
        </Card>

        {/* 3b. Open risk */}
        <RiskGauge />

        {/* 4. Quick links */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Quick actions</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <QuickLink href="/tickets/new" label="+ New ticket" />
            <QuickLink href="/screener?min_tt=6" label="Top screener setups" />
            <QuickLink href="/positions/sync" label="Sync positions" />
            <QuickLink href="/accounts/sync" label="Sync accounts" />
            <QuickLink href="/journal" label="Review journal" />
          </CardContent>
        </Card>
      </div>
    </main>
  );
}

function ChecklistRow({ ok, label, neutral }: { ok: boolean; label: string; neutral?: boolean }) {
  const Icon = neutral ? Clock : ok ? CheckCircle : XCircle;
  const cls  = neutral ? "text-muted-foreground" : ok ? "text-emerald-600 dark:text-emerald-400" : "text-destructive";
  return (
    <div className="flex items-center gap-2">
      <Icon className={`h-4 w-4 shrink-0 ${cls}`} />
      <span className={neutral ? "text-muted-foreground" : ""}>{label}</span>
    </div>
  );
}

function QuickLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="border-input hover:bg-muted inline-flex h-8 items-center rounded-md border px-3 text-xs font-medium"
    >
      {label}
    </Link>
  );
}
