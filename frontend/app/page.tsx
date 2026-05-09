import Link from "next/link";
import { ArrowRight, Activity, TrendingUp, FileText, AlertTriangle, CheckCircle, XCircle, Search, BookOpen, BarChart2 } from "lucide-react";
import { api, type Health, type HealthDb, ApiError } from "@/lib/api";

type Status = { ok: boolean };

interface Regime {
  regime: string;
  spy_price: number | null;
  spy_pct_vs_ma200: number | null;
  xiu_pct_vs_ma200: number | null;
  distribution_days: number;
  distribution_status: string;
  message: string;
}

interface MonitorStatus {
  running: boolean;
  armed_tickets: number;
  kill_switch: boolean;
  market_open: boolean;
  last_tick_at: string | null;
}

interface OpenRisk {
  total_risk_usd: string;
  total_risk_cad: string;
  risk_pct_usd: string;
  warning: string | null;
  positions: Array<{ symbol: string; open_risk_dollars: string; currency: string }>;
}

interface JournalSummary {
  total_trades: number;
  win_rate: number;
  expectancy: number;
  total_r: number;
}

async function probe(path: string): Promise<Status> {
  try {
    const res = await api<Health | HealthDb>(path);
    return { ok: res.status === "ok" };
  } catch {
    return { ok: false };
  }
}

export default async function DashboardPage() {
  const [app, regime, monitor, risk, journal] = await Promise.all([
    probe("/health"),
    api<Regime>("/api/regime").catch(() => null),
    api<MonitorStatus>("/api/monitor/status").catch(() => null),
    api<OpenRisk>("/api/journal/risk").catch(() => null),
    api<JournalSummary>("/api/journal/summary").catch(() => null),
  ]);

  const regimeBg =
    regime?.regime === "bull"    ? "from-emerald-500/8 to-transparent border-emerald-500/20" :
    regime?.regime === "caution" ? "from-amber-500/8 to-transparent border-amber-500/20"   :
    regime?.regime === "bear"    ? "from-destructive/8 to-transparent border-destructive/20" :
    "from-muted/20 to-transparent border-border";

  const regimeTextColor =
    regime?.regime === "bull"    ? "text-emerald-400" :
    regime?.regime === "caution" ? "text-amber-400"   :
    regime?.regime === "bear"    ? "text-destructive"  :
    "text-muted-foreground";

  const RegimeIcon =
    regime?.regime === "bear"    ? XCircle :
    regime?.regime === "caution" ? AlertTriangle :
    CheckCircle;

  const riskPct = risk ? Math.max(parseFloat(risk.risk_pct_usd), parseFloat(risk.total_risk_cad ? "0" : "0")) * 100 : 0;
  const usdRiskPct = risk ? parseFloat(risk.risk_pct_usd) * 100 : 0;

  return (
    <main className="min-h-screen p-6 sm:p-8 space-y-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-0.5">
            {new Date().toLocaleDateString("en-CA", { weekday: "long", month: "long", day: "numeric" })}
          </p>
        </div>
        {!app.ok && (
          <span className="text-destructive text-xs flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-destructive" />
            Backend offline
          </span>
        )}
      </div>

      {/* Regime banner — full width, most important signal */}
      {regime ? (
        <div className={`rounded-xl border bg-linear-to-r ${regimeBg} px-5 py-4`}>
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <RegimeIcon className={`h-5 w-5 shrink-0 ${regimeTextColor}`} />
              <div>
                <div className="flex items-center gap-2">
                  <span className={`text-sm font-bold uppercase tracking-widest ${regimeTextColor}`}>
                    {regime.regime}
                  </span>
                  {regime.distribution_status !== "healthy" && (
                    <span className={`text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded ${
                      regime.distribution_status === "heavy" ? "bg-destructive/20 text-destructive" : "bg-amber-500/20 text-amber-400"
                    }`}>
                      {regime.distribution_days}d dist
                    </span>
                  )}
                </div>
                <p className="text-muted-foreground text-xs mt-0.5">{regime.message}</p>
              </div>
            </div>
            <div className="flex gap-6 text-right shrink-0">
              {regime.spy_pct_vs_ma200 !== null && (
                <div>
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wide">SPY vs 200MA</div>
                  <div className={`text-sm font-semibold tabular-nums ${regime.spy_pct_vs_ma200 > 0 ? "text-emerald-400" : "text-destructive"}`}>
                    {regime.spy_pct_vs_ma200 > 0 ? "+" : ""}{regime.spy_pct_vs_ma200.toFixed(1)}%
                  </div>
                </div>
              )}
              {regime.spy_price && (
                <div>
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wide">SPY</div>
                  <div className="text-sm font-semibold tabular-nums">${regime.spy_price.toFixed(2)}</div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed bg-muted/20 px-5 py-4 text-sm text-muted-foreground">
          No market data — <Link href="/screener/scan" className="text-primary hover:underline">run a scan</Link> to load regime data.
        </div>
      )}

      {/* Key metrics row */}
      <div className="grid grid-cols-3 gap-4">
        {/* Monitor status */}
        <div className="rounded-xl border bg-card px-4 py-3 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground uppercase tracking-wide font-medium">Monitor</span>
            <span className={`h-2 w-2 rounded-full ${
              monitor?.kill_switch ? "bg-destructive" :
              monitor?.market_open ? "bg-emerald-500 animate-pulse" :
              "bg-muted-foreground/30"
            }`} />
          </div>
          <div className="flex items-end justify-between gap-2">
            <span className="text-2xl font-bold tabular-nums">{monitor?.armed_tickets ?? 0}</span>
            <span className="text-xs text-muted-foreground pb-0.5">armed tickets</span>
          </div>
          <div className="text-[11px] text-muted-foreground">
            {monitor?.kill_switch ? "Kill switch active" : monitor?.market_open ? "Polling every 15s" : "Outside market hours"}
          </div>
        </div>

        {/* Open risk */}
        <div className="rounded-xl border bg-card px-4 py-3 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-[11px] text-muted-foreground uppercase tracking-wide font-medium">Open Risk</span>
            {risk?.warning && <AlertTriangle className="h-3 w-3 text-amber-400" />}
          </div>
          <div className="flex items-end justify-between gap-2">
            <span className={`text-2xl font-bold tabular-nums ${usdRiskPct > 6 ? "text-destructive" : usdRiskPct > 4 ? "text-amber-400" : ""}`}>
              {usdRiskPct.toFixed(1)}%
            </span>
            <span className="text-xs text-muted-foreground pb-0.5">of USD equity</span>
          </div>
          <div className="h-1 rounded-full bg-muted overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${usdRiskPct > 6 ? "bg-destructive" : usdRiskPct > 4 ? "bg-amber-400" : "bg-emerald-500"}`}
              style={{ width: `${Math.min(usdRiskPct / 8 * 100, 100)}%` }}
            />
          </div>
        </div>

        {/* Journal performance */}
        {journal && journal.total_trades > 0 ? (
          <div className="rounded-xl border bg-card px-4 py-3 space-y-1">
            <span className="text-[11px] text-muted-foreground uppercase tracking-wide font-medium">Performance</span>
            <div className="flex items-end justify-between gap-2">
              <span className={`text-2xl font-bold tabular-nums ${journal.expectancy > 0 ? "text-emerald-400" : "text-destructive"}`}>
                {journal.expectancy > 0 ? "+" : ""}{journal.expectancy.toFixed(2)}R
              </span>
              <span className="text-xs text-muted-foreground pb-0.5">expectancy</span>
            </div>
            <div className="text-[11px] text-muted-foreground">
              {(journal.win_rate * 100).toFixed(0)}% win rate · {journal.total_trades} trades
            </div>
          </div>
        ) : (
          <div className="rounded-xl border bg-card px-4 py-3 space-y-1">
            <span className="text-[11px] text-muted-foreground uppercase tracking-wide font-medium">Performance</span>
            <div className="text-2xl font-bold text-muted-foreground/30">—</div>
            <div className="text-[11px] text-muted-foreground">No closed trades yet</div>
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { href: "/tickets/new", label: "New ticket",     Icon: FileText,    primary: true },
          { href: "/screener",   label: "Screener",        Icon: Search,      primary: false },
          { href: "/watchlist",  label: "Watchlist",       Icon: TrendingUp,  primary: false },
          { href: "/journal",    label: "Journal",         Icon: BookOpen,    primary: false },
        ].map(({ href, label, Icon, primary }) => (
          <Link
            key={href}
            href={href}
            className={`group flex items-center gap-2.5 rounded-xl border px-4 py-3 text-sm font-medium transition-all ${
              primary
                ? "bg-primary text-primary-foreground border-primary hover:bg-primary/90"
                : "bg-card hover:bg-muted/60 hover:border-primary/30"
            }`}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
            <ArrowRight className="h-3.5 w-3.5 ml-auto opacity-40 group-hover:opacity-70 transition-opacity" />
          </Link>
        ))}
      </div>

      {/* Open positions summary */}
      {risk && risk.positions.length > 0 && (
        <div className="rounded-xl border bg-card">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border/50">
            <span className="text-sm font-medium">Open positions</span>
            <Link href="/positions" className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">
              View all <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
          <div className="divide-y divide-border/30">
            {risk.positions.slice(0, 5).map((p, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-2.5 text-sm">
                <span className="font-mono font-medium">{p.symbol}</span>
                <span className="tabular-nums text-destructive text-xs">
                  −{new Intl.NumberFormat("en-CA", { style: "currency", currency: p.currency, maximumFractionDigits: 0 }).format(parseFloat(p.open_risk_dollars))} at risk
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Secondary nav links */}
      <div className="grid grid-cols-3 gap-2">
        {[
          { href: "/accounts",  label: "Accounts"  },
          { href: "/options",   label: "Options"   },
          { href: "/backtest",  label: "Backtest"  },
        ].map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className="rounded-lg border bg-card/50 px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-card transition-colors text-center"
          >
            {label}
          </Link>
        ))}
      </div>
    </main>
  );
}
