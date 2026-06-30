"use client";

import { useState, useEffect } from "react";
import { Play, RefreshCw, Info, Copy } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_URL } from "@/lib/api";

async function copyMarkdown(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`Copied ${label} to clipboard (${text.length.toLocaleString()} chars)`);
  } catch {
    toast.error("Couldn't access clipboard — try a different browser or use HTTPS");
  }
}

function CopyButton({ label, onCopy }: { label: string; onCopy: () => string }) {
  return (
    <button
      onClick={() => copyMarkdown(onCopy(), label)}
      className="border-input hover:bg-muted inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium"
      title="Copy as markdown — pastes nicely into chat"
    >
      <Copy className="h-3 w-3" /> Copy {label}
    </button>
  );
}


interface TierStats {
  tier: string;
  signals: number;
  triggered: number;
  trigger_rate: number;
  avg_days_to_trigger: number;
  target_hits: number;
  stop_hits: number;
  time_outs: number;
  win_rate: number;
  avg_r: number;
  avg_winner_r: number;
  avg_loser_r: number;
  win_loss_ratio: number;
  expectancy_per_signal_r: number;
  total_r: number;
  total_dollars: number;
}

interface PatternStats {
  pattern_type: string;
  signals: number;
  triggered: number;
  trigger_rate: number;
  win_rate: number;
  avg_r: number;
  avg_winner_r: number;
  avg_loser_r: number;
  win_loss_ratio: number;
  total_r: number;
  total_dollars: number;
}

interface Trade {
  symbol: string;
  signal_date: string;
  pivot_price: number;
  pattern_type: string;
  pattern_quality: number;
  buyability_at_signal: string;
  tier: string;
  tt_score: number;
  vcp_score: number;
  triggered: boolean;
  days_to_trigger: number | null;
  entry_date: string | null;
  entry_price: number | null;
  exit_date: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  r_multiple: number | null;
  dollar_pnl: number | null;
  bars_held: number | null;
}

interface BacktestOut {
  status: "idle" | "running" | "done";
  symbols_scanned: number;
  signals_found: number;
  signals_triggered: number;
  total_trades: number;
  wins: number;
  losses: number;
  scratches: number;
  win_rate: number;
  avg_r: number;
  avg_winner_r: number;
  avg_loser_r: number;
  win_loss_ratio: number;
  total_r: number;
  total_dollars: number;
  profit_factor: number;
  max_drawdown_r: number;
  max_drawdown_dollars: number;
  by_tier: TierStats[];
  by_pattern: PatternStats[];
  equity_curve: Array<{ date: string; symbol: string; r: number; cumulative_r: number; cumulative_dollars: number }>;
  trades: Trade[];
  benchmark_start_date: string | null;
  benchmark_end_date: string | null;
  benchmark_return_pct: number | null;
  benchmark_dollars: number | null;
  trades_per_month: number;
  signals_per_month: number;
  scan_id: string | null;
  used_cached_scan: boolean;
  scan_finished_at: string | null;
  scan_candidate_count: number;
}

interface SweepRow {
  value: number;
  signals_found: number;
  signals_triggered: number;
  total_trades: number;
  win_rate: number;
  avg_winner_r: number;
  avg_loser_r: number;
  win_loss_ratio: number;
  avg_r: number;
  profit_factor: number;
  total_r: number;
  total_dollars: number;
  max_drawdown_dollars: number;
}

interface SweepOut {
  sweep_param: string;
  scan_id: string;
  used_cached_scan: boolean;
  rows: SweepRow[];
}

interface SweepStatus {
  running: boolean;
  sweep: SweepOut | null;
}

const SWEEP_PRESETS: Record<string, { label: string; values: number[]; unit: string; integer: boolean }> = {
  target_r:            { label: "Target (× risk = R)",           values: [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0], unit: "R", integer: false },
  stop_atr:            { label: "Stop (× ATR-14)",               values: [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0], unit: "× ATR", integer: false },
  time_stop:           { label: "Time stop (bars after entry)",  values: [10, 15, 20, 30, 40, 60], unit: " bars", integer: true },
  trigger_window:      { label: "Trigger window (bars)",         values: [10, 20, 30, 45, 60, 90], unit: " bars", integer: true },
  pattern_quality_min: { label: "Min pattern quality (0-1)",     values: [0.30, 0.40, 0.50, 0.60, 0.70, 0.80], unit: "", integer: false },
  tt_min:              { label: "Min Trend Template (1-8)",      values: [2, 3, 4, 5, 6, 7], unit: "/8", integer: true },
};


interface PortfolioTrade {
  symbol: string;
  tier: string;
  pattern_type: string;
  entry_date: string;
  exit_date: string;
  shares: number;
  entry_price: number;
  exit_price: number;
  stop_price: number;
  target_price: number;
  r_multiple: number;
  dollar_pnl: number;
  exit_reason: string;
  bars_held: number;
  risk_dollars: number;
  notional_at_entry: number;
}

interface EquityPoint {
  date: string;
  equity: number;
  cash: number;
  open_positions: number;
  open_risk_dollars: number;
  open_risk_pct: number;
}

interface PortfolioOut {
  status: "idle" | "running" | "done";
  initial_equity: number;
  final_equity: number;
  total_return_pct: number;
  cagr_pct: number;
  max_drawdown_pct: number;
  max_drawdown_dollars: number;
  time_in_market_pct: number;
  avg_concurrent_positions: number;
  max_concurrent_positions: number;
  total_signals_considered: number;
  total_signals_triggered: number;
  total_signals_taken: number;
  signal_acceptance_rate: number;
  rejected_capital: number;
  rejected_cooldown: number;
  rejected_already_open: number;
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_winner_r: number;
  avg_loser_r: number;
  win_loss_ratio: number;
  avg_r: number;
  profit_factor: number;
  benchmark_start_date: string | null;
  benchmark_end_date: string | null;
  benchmark_return_pct: number | null;
  benchmark_dollars: number | null;
  equity_curve: EquityPoint[];
  trades: PortfolioTrade[];
  open_at_end: Array<{ symbol: string; shares: number; entry_price: number; current_price: number; unrealized_dollar_pnl: number; entry_date: string; tier: string; pattern_type: string }>;
  scan_id: string | null;
  used_cached_scan: boolean;
}

const DEFAULT_PARAMS = {
  tt_min: 4,
  pattern_quality_min: 0.5,
  stop_atr: 1.5,
  target_r: 3.0,
  time_stop: 20,
  trigger_window: 30,
  lookback_days: 504,
  account_size: 100000,
  risk_pct: 0.0075,
};

const fmtDollar = (n: number) => {
  const sign = n >= 0 ? "+" : "−";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
};
const fmtPct = (n: number, digits = 0) => `${(n * 100).toFixed(digits)}%`;

export default function BacktestPage() {
  const [tab, setTab] = useState<"single" | "sweep" | "portfolio">("single");
  const [params, setParams]   = useState(DEFAULT_PARAMS);
  const [result, setResult]   = useState<BacktestOut | null>(null);
  const [running, setRunning] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [forceRescan, setForceRescan] = useState(false);

  const poll = async () => {
    const r = await fetch(`${API_URL}/api/backtest/status`).then(x => x.json()) as BacktestOut;
    setResult(r);
    if (r.status === "running") {
      setRunning(true);
      setTimeout(poll, 3000);
    } else {
      setRunning(false);
    }
  };

  useEffect(() => { poll(); }, []);

  const run = async () => {
    setRunning(true);
    await fetch(`${API_URL}/api/backtest/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...params, force_rescan: forceRescan }),
    });
    setTimeout(poll, 2000);
  };

  const set = (k: string, v: string) =>
    setParams(p => ({ ...p, [k]: parseFloat(v) || parseInt(v) || 0 }));

  return (
    <main className="container mx-auto max-w-7xl p-6 sm:p-10">
      <header className="mb-6">
        <h1 className="text-3xl font-semibold tracking-tight">Backtest</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          What would have happened if you'd followed the screener for the last 2 years?
        </p>
        <button
          onClick={() => setShowHelp(s => !s)}
          className="text-muted-foreground hover:text-foreground mt-2 inline-flex items-center gap-1 text-xs"
        >
          <Info className="h-3 w-3" /> {showHelp ? "Hide" : "How this works"}
        </button>
        {showHelp && (
          <Card className="bg-muted/30 mt-3">
            <CardContent className="space-y-2 pt-4 text-sm">
              <p>
                The screener identifies setups (S/A/B tier) with a specific <strong>pivot price</strong> —
                the level where you'd place a buy-stop. The backtest replays every signal that fired
                historically and asks <em>two</em> questions:
              </p>
              <ol className="ml-4 list-decimal space-y-1">
                <li>
                  <strong>Trigger:</strong> Did the stock actually reach the pivot within {params.trigger_window} days
                  (would your buy-stop have filled)?
                </li>
                <li>
                  <strong>Outcome:</strong> If filled, did the trade hit your target ({params.target_r}R)
                  or your stop ({params.stop_atr}× ATR) first?
                </li>
              </ol>
              <p className="text-muted-foreground text-xs">
                Results show in <strong>R-multiples</strong> (multiples of risk-per-trade) and in <strong>dollars</strong>
                {" "}assuming a ${params.account_size.toLocaleString()} account risking{" "}
                {(params.risk_pct * 100).toFixed(2)}% per trade. Benchmark = SPY buy-and-hold for the same window.
                Universe is only currently-active screener symbols, so results are slightly optimistic vs. reality
                (delisted/blown-up names are missing — &quot;survivorship bias&quot;).
              </p>
            </CardContent>
          </Card>
        )}
      </header>

      {/* Tabs */}
      <div className="mb-6 border-b">
        <div className="flex gap-1">
          {([
            ["single", "Single run"],
            ["sweep", "Parameter sweep"],
            ["portfolio", "Portfolio sim"],
          ] as const).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                tab === key
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {tab === "sweep" && <SweepTab baseParams={params} />}
      {tab === "portfolio" && <PortfolioTab baseParams={params} />}

      {tab === "single" && (
      <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
        {/* Params */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Parameters</CardTitle>
              <CardDescription className="text-xs">
                Uses the 2 years of daily bars already in your DB.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {[
                { key: "account_size",        label: "Account size ($)",            step: "1000",  min: "1000",  max: "10000000" },
                { key: "risk_pct",            label: "Risk per trade (frac)",       step: "0.0025", min: "0.001", max: "0.05" },
                { key: "trigger_window",      label: "Trigger window (bars)",       step: "5",     min: "5",     max: "120" },
                { key: "stop_atr",            label: "Stop (× ATR-14)",             step: "0.25",  min: "0.5",   max: "5" },
                { key: "target_r",            label: "Target (× risk = R)",         step: "0.5",   min: "1",     max: "10" },
                { key: "time_stop",           label: "Time stop (bars after entry)",step: "1",     min: "5",     max: "60" },
                { key: "pattern_quality_min", label: "Min pattern quality (0-1)",   step: "0.05",  min: "0",     max: "1" },
                { key: "tt_min",              label: "Min Trend Template (1-8)",    step: "1",     min: "1",     max: "8" },
                { key: "lookback_days",       label: "Lookback (trading days)",     step: "63",    min: "126",   max: "504" },
              ].map(({ key, label, step, min, max }) => (
                <div key={key} className="flex flex-col gap-1">
                  <Label className="text-xs">{label}</Label>
                  <Input
                    type="number"
                    step={step}
                    min={min}
                    max={max}
                    value={(params as any)[key]}
                    onChange={e => set(key, e.target.value)}
                    className="tabular-nums text-sm h-8"
                  />
                </div>
              ))}

              <label className="flex items-center gap-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  checked={forceRescan}
                  onChange={e => setForceRescan(e.target.checked)}
                  className="h-3.5 w-3.5"
                />
                <span title="Skip cached signal scan and re-detect from scratch (slow, ~25 min for full universe)">
                  Force re-scan (slow)
                </span>
              </label>

              <button
                onClick={run}
                disabled={running}
                className="bg-primary text-primary-foreground w-full flex items-center justify-center gap-2 rounded-md py-2 text-sm font-medium disabled:opacity-50"
              >
                {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {running ? "Running…" : "Run backtest"}
              </button>

              {result?.status === "done" && (
                <p className="text-muted-foreground text-xs text-center">
                  Scanned {result.symbols_scanned} symbols · {result.signals_found} signals · {result.total_trades} trades
                </p>
              )}

              {result?.status === "done" && result.scan_finished_at && (
                <div className={`text-xs rounded-md p-2 ${
                  result.used_cached_scan
                    ? "bg-emerald-50 dark:bg-emerald-950/20 text-emerald-700 dark:text-emerald-400"
                    : "bg-muted/40 text-muted-foreground"
                }`}>
                  {result.used_cached_scan ? "✓ Used cached scan" : "⟳ Built fresh scan"} from{" "}
                  {new Date(result.scan_finished_at).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}
                  {" · "}{result.scan_candidate_count.toLocaleString()} candidates
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Results */}
        <div className="space-y-4">
          {running && (
            <Card>
              <CardContent className="py-12 text-center text-sm text-muted-foreground">
                <RefreshCw className="h-6 w-6 animate-spin mx-auto mb-3" />
                Simulating across the full universe… 20-30 minutes for a full ~7k-symbol scan
                (pattern detection runs per-bar). Page polls every 3s for results.
              </CardContent>
            </Card>
          )}

          {!running && result?.status === "done" && result.signals_found > 0 && (
            <>
              <div className="flex justify-end">
                <CopyButton
                  label="single-run summary"
                  onCopy={() => buildSingleRunMarkdown(params, result)}
                />
              </div>

              {/* Headline cards */}
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="Strategy P&L"
                  value={fmtDollar(result.total_dollars)}
                  sub={`${result.total_r >= 0 ? "+" : ""}${result.total_r.toFixed(1)}R · ${result.total_trades} trades`}
                  color={result.total_dollars >= 0 ? "green" : "red"}
                />
                <StatCard
                  label="SPY buy & hold"
                  value={result.benchmark_dollars !== null ? fmtDollar(result.benchmark_dollars) : "—"}
                  sub={result.benchmark_return_pct !== null
                    ? `${result.benchmark_return_pct >= 0 ? "+" : ""}${result.benchmark_return_pct.toFixed(1)}% · ${result.benchmark_start_date ?? ""} → ${result.benchmark_end_date ?? ""}`
                    : "no SPY data"}
                  color="amber"
                />
                <StatCard
                  label="Batting average"
                  value={fmtPct(result.win_rate)}
                  sub={`${result.wins} wins / ${result.losses} losses · Profit factor ${result.profit_factor.toFixed(2)}`}
                  color={result.win_rate >= 0.5 ? "green" : result.win_rate >= 0.35 ? "amber" : "red"}
                />
                <StatCard
                  label="Avg win / Avg loss"
                  value={
                    result.avg_winner_r !== 0 || result.avg_loser_r !== 0
                      ? `+${result.avg_winner_r.toFixed(2)}R / ${result.avg_loser_r.toFixed(2)}R`
                      : "—"
                  }
                  sub={
                    result.win_loss_ratio > 0
                      ? `Win/Loss ratio ${result.win_loss_ratio.toFixed(2)}× (Minervini target ≥ 2.0×)`
                      : "—"
                  }
                  color={result.win_loss_ratio >= 2 ? "green" : result.win_loss_ratio >= 1.5 ? "amber" : "red"}
                />
                <StatCard
                  label="Frequency"
                  value={`${result.trades_per_month.toFixed(1)}/mo`}
                  sub={`${result.signals_per_month.toFixed(1)} signals/mo`}
                />
              </div>

              {/* Tier table — the headline */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Performance by tier</CardTitle>
                  <CardDescription className="text-xs">
                    Each row = the expected outcome of acting on one tier&apos;s signals. R = R-multiple,
                    a multiple of the dollar amount you risk per trade. Hover any column header for the full meaning.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-muted-foreground border-b uppercase">
                          <Th>Tier</Th>
                          <Th align="right" tip="Number of times this tier fired in the backtest window">Signals</Th>
                          <Th align="right" tip="% of signals where the stock actually reached the buy-stop">Trigger rate</Th>
                          <Th align="right" tip="Average trading days from signal to triggered fill">Days to fill</Th>
                          <Th align="right" tip="% of triggered trades that ended in profit (Minervini's 'batting average')">Batting avg</Th>
                          <Th align="right" tip="Average win amount as a multiple of risk. If you risked $750 and avg win is +2R, that's $1,500 per winner">Avg win</Th>
                          <Th align="right" tip="Average loss amount as a multiple of risk. -1R = full stop hit; smaller = exited early">Avg loss</Th>
                          <Th align="right" tip="Avg win ÷ |Avg loss|. Minervini target ≥ 2.0 (winners are at least 2× the size of losers)">Win/Loss ratio</Th>
                          <Th align="right" tip="Mean R across all triggered trades (winners + losers + scratches mixed). This × Total trades = Total R">Avg R per trade</Th>
                          <Th align="right" tip="Average R per signal you SEE, not per trade — already counts non-triggers as zero. Best apples-to-apples tier comparison">EV per signal</Th>
                          <Th align="right" tip="Sum of all R-multiples across triggered trades">Total R</Th>
                          <Th align="right" tip="Total R × dollars-per-trade. NOTE: assumes infinite capital — see banner about portfolio realism">Total $</Th>
                          <Th align="right" tip="Number of trades that hit the profit target">Targets</Th>
                          <Th align="right" tip="Number of trades that hit the stop loss">Stops</Th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {result.by_tier.map(t => {
                          const tierColor = t.tier === "S" ? "text-emerald-600 dark:text-emerald-400"
                            : t.tier === "A" ? "text-blue-600 dark:text-blue-400"
                            : t.tier === "B" ? "text-amber-600 dark:text-amber-400"
                            : "";
                          return (
                            <tr key={t.tier}>
                              <td className={`py-2 font-mono font-bold ${tierColor}`}>{t.tier}</td>
                              <td className="py-2 text-right tabular-nums">{t.signals}</td>
                              <td className="py-2 text-right tabular-nums">{t.signals > 0 ? fmtPct(t.trigger_rate) : "—"}</td>
                              <td className="py-2 text-right tabular-nums">{t.triggered > 0 ? t.avg_days_to_trigger.toFixed(0) : "—"}</td>
                              <td className={`py-2 text-right tabular-nums ${
                                t.triggered === 0 ? "" :
                                t.win_rate >= 0.5 ? "text-emerald-600 dark:text-emerald-400" :
                                t.win_rate >= 0.35 ? "text-amber-600 dark:text-amber-400" :
                                "text-destructive"
                              }`}>{t.triggered > 0 ? fmtPct(t.win_rate) : "—"}</td>
                              <td className="py-2 text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                                {t.avg_winner_r !== 0 ? `+${t.avg_winner_r.toFixed(2)}R` : "—"}
                              </td>
                              <td className="py-2 text-right tabular-nums text-destructive">
                                {t.avg_loser_r !== 0 ? `${t.avg_loser_r.toFixed(2)}R` : "—"}
                              </td>
                              <td className={`py-2 text-right tabular-nums font-medium ${
                                t.win_loss_ratio === 0 ? "" :
                                t.win_loss_ratio >= 2 ? "text-emerald-600 dark:text-emerald-400" :
                                t.win_loss_ratio >= 1.5 ? "text-amber-600 dark:text-amber-400" :
                                "text-destructive"
                              }`}>{t.win_loss_ratio > 0 ? `${t.win_loss_ratio.toFixed(2)}×` : "—"}</td>
                              <td className={`py-2 text-right tabular-nums ${
                                t.triggered === 0 ? "" :
                                t.avg_r > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                              }`}>{t.triggered > 0 ? `${t.avg_r > 0 ? "+" : ""}${t.avg_r.toFixed(2)}R` : "—"}</td>
                              <td className={`py-2 text-right tabular-nums font-medium ${
                                t.signals === 0 ? "" :
                                t.expectancy_per_signal_r > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                              }`}>{t.signals > 0 ? `${t.expectancy_per_signal_r > 0 ? "+" : ""}${t.expectancy_per_signal_r.toFixed(2)}R` : "—"}</td>
                              <td className={`py-2 text-right tabular-nums ${
                                t.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : t.total_r < 0 ? "text-destructive" : ""
                              }`}>{t.signals > 0 ? `${t.total_r > 0 ? "+" : ""}${t.total_r.toFixed(1)}R` : "—"}</td>
                              <td className={`py-2 text-right tabular-nums font-medium ${
                                t.total_dollars > 0 ? "text-emerald-600 dark:text-emerald-400" : t.total_dollars < 0 ? "text-destructive" : ""
                              }`}>{t.triggered > 0 ? fmtDollar(t.total_dollars) : "—"}</td>
                              <td className="py-2 text-right tabular-nums">{t.target_hits}</td>
                              <td className="py-2 text-right tabular-nums">{t.stop_hits}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-muted-foreground mt-3 text-xs">
                    <strong>Vs live screener:</strong> backtest tier counts will be larger because we
                    can&apos;t apply the live screener&apos;s RS-rank gate (S≥85, A≥75, B≥70) — historical RS
                    rank isn&apos;t stored per-bar. The pattern set and quality thresholds match. Tier A
                    also doesn&apos;t apply the &quot;accelerating earnings&quot; filter (no historical
                    earnings snapshots). So the backtest measures pattern edge, not the full
                    live-screener funnel.
                  </p>
                </CardContent>
              </Card>

              {/* Pattern table */}
              {result.by_pattern.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Performance by pattern</CardTitle>
                    <CardDescription className="text-xs">
                      Same metrics broken down by chart pattern instead of tier. Hover any header for definition.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-muted-foreground border-b uppercase">
                            <Th>Pattern</Th>
                            <Th align="right" tip="Number of times this pattern fired in the backtest window">Signals</Th>
                            <Th align="right" tip="% of signals where the stock actually reached the buy-stop">Trigger rate</Th>
                            <Th align="right" tip="% of triggered trades that ended in profit">Batting avg</Th>
                            <Th align="right" tip="Average win amount as a multiple of risk">Avg win</Th>
                            <Th align="right" tip="Average loss amount as a multiple of risk (negative)">Avg loss</Th>
                            <Th align="right" tip="Avg win ÷ |Avg loss|. Minervini target ≥ 2.0">Win/Loss ratio</Th>
                            <Th align="right" tip="Mean R across all triggered trades (winners + losers + scratches)">Avg R per trade</Th>
                            <Th align="right" tip="Sum of all R-multiples across triggered trades">Total R</Th>
                            <Th align="right" tip="Total R × dollars-per-trade. Assumes infinite capital — see banner">Total $</Th>
                          </tr>
                        </thead>
                        <tbody className="divide-y">
                          {result.by_pattern.map(p => (
                            <tr key={p.pattern_type}>
                              <td className="py-2 font-medium">{prettyPattern(p.pattern_type)}</td>
                              <td className="py-2 text-right tabular-nums">{p.signals}</td>
                              <td className="py-2 text-right tabular-nums">{fmtPct(p.trigger_rate)}</td>
                              <td className={`py-2 text-right tabular-nums ${
                                p.triggered === 0 ? "" :
                                p.win_rate >= 0.5 ? "text-emerald-600 dark:text-emerald-400" :
                                p.win_rate >= 0.35 ? "text-amber-600 dark:text-amber-400" :
                                "text-destructive"
                              }`}>{p.triggered > 0 ? fmtPct(p.win_rate) : "—"}</td>
                              <td className="py-2 text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                                {p.avg_winner_r !== 0 ? `+${p.avg_winner_r.toFixed(2)}R` : "—"}
                              </td>
                              <td className="py-2 text-right tabular-nums text-destructive">
                                {p.avg_loser_r !== 0 ? `${p.avg_loser_r.toFixed(2)}R` : "—"}
                              </td>
                              <td className={`py-2 text-right tabular-nums font-medium ${
                                p.win_loss_ratio === 0 ? "" :
                                p.win_loss_ratio >= 2 ? "text-emerald-600 dark:text-emerald-400" :
                                p.win_loss_ratio >= 1.5 ? "text-amber-600 dark:text-amber-400" :
                                "text-destructive"
                              }`}>{p.win_loss_ratio > 0 ? `${p.win_loss_ratio.toFixed(2)}×` : "—"}</td>
                              <td className={`py-2 text-right tabular-nums ${
                                p.triggered === 0 ? "" :
                                p.avg_r > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                              }`}>{p.triggered > 0 ? `${p.avg_r > 0 ? "+" : ""}${p.avg_r.toFixed(2)}R` : "—"}</td>
                              <td className={`py-2 text-right tabular-nums ${
                                p.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : p.total_r < 0 ? "text-destructive" : ""
                              }`}>{`${p.total_r > 0 ? "+" : ""}${p.total_r.toFixed(1)}R`}</td>
                              <td className={`py-2 text-right tabular-nums font-medium ${
                                p.total_dollars > 0 ? "text-emerald-600 dark:text-emerald-400" : p.total_dollars < 0 ? "text-destructive" : ""
                              }`}>{p.triggered > 0 ? fmtDollar(p.total_dollars) : "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Equity curve */}
              {result.equity_curve.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Strategy equity curve</CardTitle>
                    <CardDescription className="text-xs">
                      Cumulative $ P&L if you took every triggered trade.
                      {result.benchmark_dollars !== null && (
                        <> SPY buy-and-hold over the same window: {fmtDollar(result.benchmark_dollars)}.</>
                      )}
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <EquityMiniChart
                      curve={result.equity_curve}
                      benchmarkFinal={result.benchmark_dollars}
                    />
                    <div className="text-muted-foreground mt-2 grid grid-cols-2 gap-3 text-xs md:grid-cols-4">
                      <div>Final: <span className={result.total_dollars >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}>{fmtDollar(result.total_dollars)}</span></div>
                      <div>Max DD: <span className="text-destructive">−${result.max_drawdown_dollars.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span></div>
                      <div>Trades: {result.total_trades}</div>
                      <div>Profit factor: {result.profit_factor.toFixed(2)}</div>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Trade log */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Signal log (first 300)</CardTitle>
                  <CardDescription className="text-xs">
                    Includes both triggered and non-triggered signals. Non-triggers show as &quot;—&quot;.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-muted-foreground border-b uppercase">
                          <th className="pb-2 text-left">Symbol</th>
                          <th className="pb-2 text-left">Signal</th>
                          <th className="pb-2 text-left">Tier</th>
                          <th className="pb-2 text-left">Pattern</th>
                          <th className="pb-2 text-right">Pivot</th>
                          <th className="pb-2 text-right">Trig?</th>
                          <th className="pb-2 text-right">Days</th>
                          <th className="pb-2 text-right">Entry</th>
                          <th className="pb-2 text-right">Exit</th>
                          <th className="pb-2 text-right">R</th>
                          <th className="pb-2 text-right">$</th>
                          <th className="pb-2 text-right">Reason</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {result.trades.map((t, i) => (
                          <tr key={i}>
                            <td className="py-1 font-mono font-medium">{t.symbol}</td>
                            <td className="py-1 tabular-nums">{t.signal_date}</td>
                            <td className="py-1 font-mono font-bold">{t.tier || "—"}</td>
                            <td className="py-1">{prettyPattern(t.pattern_type)}</td>
                            <td className="py-1 text-right tabular-nums">{t.pivot_price.toFixed(2)}</td>
                            <td className={`py-1 text-right ${t.triggered ? "" : "text-muted-foreground"}`}>{t.triggered ? "✓" : "—"}</td>
                            <td className="py-1 text-right tabular-nums">{t.days_to_trigger ?? "—"}</td>
                            <td className="py-1 text-right tabular-nums">{t.entry_price?.toFixed(2) ?? "—"}</td>
                            <td className="py-1 text-right tabular-nums">{t.exit_price?.toFixed(2) ?? "—"}</td>
                            <td className={`py-1 text-right tabular-nums font-medium ${
                              t.r_multiple === null ? "text-muted-foreground" :
                              t.r_multiple > 0.1 ? "text-emerald-600 dark:text-emerald-400" :
                              t.r_multiple < -0.05 ? "text-destructive" : ""
                            }`}>{t.r_multiple !== null ? `${t.r_multiple > 0 ? "+" : ""}${t.r_multiple.toFixed(2)}R` : "—"}</td>
                            <td className={`py-1 text-right tabular-nums ${
                              t.dollar_pnl === null ? "text-muted-foreground" :
                              t.dollar_pnl > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                            }`}>{t.dollar_pnl !== null ? fmtDollar(t.dollar_pnl) : "—"}</td>
                            <td className="py-1 text-right capitalize">{t.exit_reason ?? (t.triggered ? "" : "no-trigger")}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </>
          )}

          {!running && result?.status === "done" && result.signals_found === 0 && (
            <Card>
              <CardContent className="py-12 text-center text-sm text-muted-foreground">
                No signals found with these parameters. Try lowering Min pattern quality or Min Trend Template,
                or run a screener scan first to populate more price data.
              </CardContent>
            </Card>
          )}

          {!running && (!result || result.status === "idle") && (
            <Card>
              <CardContent className="py-12 text-center text-sm text-muted-foreground">
                Set parameters and click <strong>Run backtest</strong>. Uses the 2 years of daily
                bars already in your database — no additional data download needed.
              </CardContent>
            </Card>
          )}
        </div>
      </div>
      )}
    </main>
  );
}

function SweepTab({ baseParams }: { baseParams: typeof DEFAULT_PARAMS }) {
  const [sweepParam, setSweepParam] = useState<string>("target_r");
  const [valuesStr, setValuesStr] = useState<string>(SWEEP_PRESETS["target_r"].values.join(", "));
  const [status, setStatus] = useState<SweepStatus | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const presetForParam = SWEEP_PRESETS[sweepParam];

  const poll = async () => {
    try {
      const r = await fetch(`${API_URL}/api/backtest/sweep/status`).then(x => x.json()) as SweepStatus;
      setStatus(r);
      if (r.running) {
        setTimeout(poll, 2000);
      }
    } catch {/* ignore */}
  };

  useEffect(() => { poll(); }, []);

  const changeParam = (k: string) => {
    setSweepParam(k);
    setValuesStr(SWEEP_PRESETS[k].values.join(", "));
  };

  const runSweep = async () => {
    const values = valuesStr
      .split(",")
      .map(s => parseFloat(s.trim()))
      .filter(v => !isNaN(v));
    if (values.length < 2) {
      alert("Need at least 2 values separated by commas");
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/backtest/sweep`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          base_params: baseParams,
          sweep_param: sweepParam,
          sweep_values: values,
        }),
      });
      if (!res.ok) {
        const txt = await res.text();
        alert(`Sweep failed: ${txt}`);
        return;
      }
      setTimeout(poll, 1000);
    } finally {
      setSubmitting(false);
    }
  };

  const sweep = status?.sweep;
  const running = status?.running ?? false;
  const matchesCurrentParam = sweep && sweep.sweep_param === sweepParam;

  // Identify the best row by avg R per trade (or by total_dollars when ties)
  const bestIndex = matchesCurrentParam && sweep.rows.length > 0
    ? sweep.rows.reduce((best, r, i, arr) => r.avg_r > arr[best].avg_r ? i : best, 0)
    : -1;

  return (
    <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Sweep configuration</CardTitle>
            <CardDescription className="text-xs">
              Vary one parameter; everything else is held at the values from the Single run tab.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Parameter to vary</Label>
              <select
                value={sweepParam}
                onChange={e => changeParam(e.target.value)}
                className="bg-background border-input h-9 rounded-md border px-2 text-sm"
              >
                {Object.entries(SWEEP_PRESETS).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Values to test (comma-separated)</Label>
              <Input
                value={valuesStr}
                onChange={e => setValuesStr(e.target.value)}
                placeholder={presetForParam.values.join(", ")}
                className="tabular-nums text-sm h-8"
              />
              <p className="text-muted-foreground text-xs">
                Default preset shown. Each value runs a fast Phase-2 simulation against the cached scan.
              </p>
            </div>

            <button
              onClick={runSweep}
              disabled={submitting || running}
              className="bg-primary text-primary-foreground w-full flex items-center justify-center gap-2 rounded-md py-2 text-sm font-medium disabled:opacity-50"
            >
              {running || submitting ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {running ? "Sweeping…" : "Run sweep"}
            </button>
            <p className="text-muted-foreground text-xs">
              <strong>Tip:</strong> if no cached scan exists yet, the first sweep will build one
              (~25 min). After that, each sweep runs in seconds.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        {running && (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              <RefreshCw className="h-5 w-5 animate-spin mx-auto mb-2" />
              Running sweep… polling for results.
            </CardContent>
          </Card>
        )}
        {!running && !sweep && (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Configure a sweep on the left and click <strong>Run sweep</strong>.
            </CardContent>
          </Card>
        )}
        {sweep && (
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="text-base">
                    Sweep over {SWEEP_PRESETS[sweep.sweep_param]?.label ?? sweep.sweep_param}
                  </CardTitle>
                  <CardDescription className="text-xs">
                    Best row by <em>Avg R per trade</em> highlighted. {sweep.used_cached_scan ? "Used cached scan." : "Built fresh scan."}
                  </CardDescription>
                </div>
                <CopyButton label="sweep table" onCopy={() => buildSweepMarkdown(sweep, baseParams)} />
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-muted-foreground border-b uppercase">
                      <Th>{SWEEP_PRESETS[sweep.sweep_param]?.label.split("(")[0].trim() ?? sweep.sweep_param}</Th>
                      <Th align="right" tip="Total signals at this parameter value">Signals</Th>
                      <Th align="right" tip="Triggered (filled) trades at this value">Trades</Th>
                      <Th align="right" tip="% of triggered trades that ended in profit">Batting avg</Th>
                      <Th align="right" tip="Average win as multiple of risk">Avg win</Th>
                      <Th align="right" tip="Average loss as multiple of risk (negative)">Avg loss</Th>
                      <Th align="right" tip="Avg win ÷ |Avg loss|. Minervini target ≥ 2.0">Win/Loss ratio</Th>
                      <Th align="right" tip="Mean R across all triggered trades">Avg R per trade</Th>
                      <Th align="right" tip="Total $ won ÷ total $ lost. Above 1.0 = profitable">Profit factor</Th>
                      <Th align="right" tip="Sum of R-multiples across all triggered trades">Total R</Th>
                      <Th align="right" tip="Total dollars (assumes infinite capital — comparative only)">Total $</Th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {sweep.rows.map((r, i) => {
                      const isBest = i === bestIndex;
                      const unit = SWEEP_PRESETS[sweep.sweep_param]?.unit ?? "";
                      const isInt = SWEEP_PRESETS[sweep.sweep_param]?.integer ?? false;
                      return (
                        <tr key={i} className={isBest ? "bg-emerald-50/50 dark:bg-emerald-950/20 font-medium" : ""}>
                          <td className="py-2 tabular-nums">
                            {isBest && "★ "}
                            {isInt ? r.value.toFixed(0) : r.value.toFixed(2)}{unit}
                          </td>
                          <td className="py-2 text-right tabular-nums">{r.signals_found.toLocaleString()}</td>
                          <td className="py-2 text-right tabular-nums">{r.total_trades.toLocaleString()}</td>
                          <td className={`py-2 text-right tabular-nums ${
                            r.win_rate >= 0.5 ? "text-emerald-600 dark:text-emerald-400" :
                            r.win_rate >= 0.35 ? "text-amber-600 dark:text-amber-400" :
                            "text-destructive"
                          }`}>{fmtPct(r.win_rate)}</td>
                          <td className="py-2 text-right tabular-nums text-emerald-600 dark:text-emerald-400">
                            {r.avg_winner_r !== 0 ? `+${r.avg_winner_r.toFixed(2)}R` : "—"}
                          </td>
                          <td className="py-2 text-right tabular-nums text-destructive">
                            {r.avg_loser_r !== 0 ? `${r.avg_loser_r.toFixed(2)}R` : "—"}
                          </td>
                          <td className={`py-2 text-right tabular-nums ${
                            r.win_loss_ratio >= 2 ? "text-emerald-600 dark:text-emerald-400" :
                            r.win_loss_ratio >= 1.5 ? "text-amber-600 dark:text-amber-400" :
                            "text-destructive"
                          }`}>{r.win_loss_ratio > 0 ? `${r.win_loss_ratio.toFixed(2)}×` : "—"}</td>
                          <td className={`py-2 text-right tabular-nums ${
                            r.avg_r > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                          }`}>{`${r.avg_r > 0 ? "+" : ""}${r.avg_r.toFixed(2)}R`}</td>
                          <td className={`py-2 text-right tabular-nums ${
                            r.profit_factor >= 1.5 ? "text-emerald-600 dark:text-emerald-400" :
                            r.profit_factor >= 1 ? "text-amber-600 dark:text-amber-400" :
                            "text-destructive"
                          }`}>{r.profit_factor.toFixed(2)}</td>
                          <td className={`py-2 text-right tabular-nums ${
                            r.total_r > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                          }`}>{`${r.total_r > 0 ? "+" : ""}${r.total_r.toFixed(1)}R`}</td>
                          <td className={`py-2 text-right tabular-nums ${
                            r.total_dollars > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                          }`}>{fmtDollar(r.total_dollars)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function PortfolioTab({ baseParams }: { baseParams: typeof DEFAULT_PARAMS }) {
  const [maxConcurrent, setMaxConcurrent] = useState(10);
  const [maxOpenRisk, setMaxOpenRisk] = useState(0.08);
  const [cooldownBars, setCooldownBars] = useState(5);
  const [status, setStatus] = useState<PortfolioOut | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const poll = async () => {
    try {
      const r = await fetch(`${API_URL}/api/backtest/portfolio/status`).then(x => x.json()) as PortfolioOut;
      setStatus(r);
      if (r.status === "running") setTimeout(poll, 3000);
    } catch {/* ignore */}
  };

  useEffect(() => { poll(); }, []);

  const run = async () => {
    setSubmitting(true);
    try {
      const body = {
        ...baseParams,
        max_concurrent_positions: maxConcurrent,
        max_total_open_risk_pct: maxOpenRisk,
        cooldown_bars_after_exit: cooldownBars,
      };
      await fetch(`${API_URL}/api/backtest/portfolio`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      setTimeout(poll, 1500);
    } finally {
      setSubmitting(false);
    }
  };

  const running = status?.status === "running";
  const done = status?.status === "done";

  return (
    <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Portfolio constraints</CardTitle>
            <CardDescription className="text-xs">
              All other parameters (target R, stop, etc.) come from the Single run tab.
              This sim respects capital limits — sized off current equity, capped at concurrent positions and total open risk.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-1">
              <Label className="text-xs" title="Max number of positions held simultaneously">
                Max concurrent positions
              </Label>
              <Input
                type="number" min={1} max={50} step={1}
                value={maxConcurrent}
                onChange={e => setMaxConcurrent(parseInt(e.target.value) || 10)}
                className="h-9 tabular-nums"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs" title="Cap on summed (entry-stop) × shares across open positions, divided by current equity. Minervini's rule of thumb: 8%">
                Max total open risk (fraction)
              </Label>
              <Input
                type="number" min={0.01} max={0.30} step={0.01}
                value={maxOpenRisk}
                onChange={e => setMaxOpenRisk(parseFloat(e.target.value) || 0.08)}
                className="h-9 tabular-nums"
              />
              <p className="text-muted-foreground text-xs">
                {(maxOpenRisk * 100).toFixed(1)}% — at risk per trade {(baseParams.risk_pct * 100).toFixed(2)}%,
                this caps you at ~{Math.floor(maxOpenRisk / baseParams.risk_pct)} simultaneous positions from the risk side
              </p>
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs" title="After exiting a position, how many bars to wait before re-entering the same symbol">
                Cooldown after exit (bars)
              </Label>
              <Input
                type="number" min={0} max={60} step={1}
                value={cooldownBars}
                onChange={e => setCooldownBars(parseInt(e.target.value) || 5)}
                className="h-9 tabular-nums"
              />
            </div>

            <button
              onClick={run}
              disabled={submitting || running}
              className="bg-primary text-primary-foreground w-full flex items-center justify-center gap-2 rounded-md py-2 text-sm font-medium disabled:opacity-50"
            >
              {running || submitting ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              {running ? "Simulating…" : "Run portfolio sim"}
            </button>
            <p className="text-muted-foreground text-xs">
              If no cached signal scan exists, the first run builds one (~25 min).
              Subsequent runs reuse it and finish in seconds.
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        {running && (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              <RefreshCw className="h-5 w-5 animate-spin mx-auto mb-2" />
              Simulating portfolio across the universe…
            </CardContent>
          </Card>
        )}
        {!status || (status.status === "idle" && !running) ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              Configure portfolio constraints and click <strong>Run portfolio sim</strong>.
              Answers &quot;what would my $100k have become if I&apos;d traded this strategy with realistic capital limits?&quot;
            </CardContent>
          </Card>
        ) : null}

        {done && status && (
          <>
            <div className="flex justify-end">
              <CopyButton
                label="portfolio summary"
                onCopy={() => buildPortfolioMarkdown(
                  baseParams,
                  { maxConcurrent, maxOpenRisk, cooldownBars },
                  status,
                )}
              />
            </div>
            {/* Headline cards */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatCard
                label="Final equity"
                value={`$${status.final_equity.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                sub={`Started $${status.initial_equity.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                color={status.final_equity >= status.initial_equity ? "green" : "red"}
              />
              <StatCard
                label="Total return"
                value={`${status.total_return_pct >= 0 ? "+" : ""}${status.total_return_pct.toFixed(1)}%`}
                sub={`CAGR ${status.cagr_pct >= 0 ? "+" : ""}${status.cagr_pct.toFixed(1)}%`}
                color={status.total_return_pct >= 0 ? "green" : "red"}
              />
              <StatCard
                label="Max drawdown"
                value={`-${status.max_drawdown_pct.toFixed(1)}%`}
                sub={`-$${status.max_drawdown_dollars.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                color="red"
              />
              <StatCard
                label="SPY buy & hold"
                value={status.benchmark_return_pct !== null ? `${status.benchmark_return_pct >= 0 ? "+" : ""}${status.benchmark_return_pct.toFixed(1)}%` : "—"}
                sub={status.benchmark_dollars !== null ? `$${(status.initial_equity + status.benchmark_dollars).toLocaleString("en-US", { maximumFractionDigits: 0 })} ending` : "—"}
                color="amber"
              />
            </div>

            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatCard label="Closed trades" value={String(status.closed_trades)} sub={`${status.wins} wins / ${status.losses} losses`} />
              <StatCard
                label="Batting average"
                value={`${(status.win_rate * 100).toFixed(1)}%`}
                sub={`Profit factor ${status.profit_factor.toFixed(2)}`}
                color={status.win_rate >= 0.5 ? "green" : status.win_rate >= 0.35 ? "amber" : "red"}
              />
              <StatCard
                label="Avg win / Avg loss"
                value={`+${status.avg_winner_r.toFixed(2)}R / ${status.avg_loser_r.toFixed(2)}R`}
                sub={`Ratio ${status.win_loss_ratio.toFixed(2)}× (Minervini target ≥ 2.0)`}
                color={status.win_loss_ratio >= 2 ? "green" : status.win_loss_ratio >= 1.5 ? "amber" : "red"}
              />
              <StatCard
                label="Time in market"
                value={`${status.time_in_market_pct.toFixed(0)}%`}
                sub={`Avg ${status.avg_concurrent_positions.toFixed(1)} / Max ${status.max_concurrent_positions} positions`}
              />
            </div>

            {/* Signal acceptance breakdown */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Signal acceptance</CardTitle>
                <CardDescription className="text-xs">
                  How many signals the portfolio could actually act on. Low acceptance = capital is the bottleneck.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-3 md:grid-cols-5 text-sm">
                  <Stat label="Considered" value={status.total_signals_considered.toLocaleString()} />
                  <Stat label="Triggered" value={status.total_signals_triggered.toLocaleString()} />
                  <Stat
                    label="Taken"
                    value={status.total_signals_taken.toLocaleString()}
                    sub={`${(status.signal_acceptance_rate * 100).toFixed(1)}% of triggered`}
                    color={status.signal_acceptance_rate > 0.5 ? "green" : "amber"}
                  />
                  <Stat label="Skipped: already in symbol" value={status.rejected_already_open.toLocaleString()} />
                  <Stat
                    label="Skipped: capital full"
                    value={status.rejected_capital.toLocaleString()}
                    color={status.rejected_capital > status.total_signals_taken / 2 ? "red" : undefined}
                  />
                </div>
                <p className="text-muted-foreground mt-3 text-xs">
                  {status.rejected_capital > status.total_signals_taken * 0.5
                    ? "⚠ You're turning away more than half your signals due to capital limits. Consider raising max concurrent or max open risk."
                    : status.signal_acceptance_rate > 0.6
                    ? "✓ Capital wasn't a significant bottleneck — most signals got acted on."
                    : ""}
                </p>
              </CardContent>
            </Card>

            {/* Equity curve */}
            {status.equity_curve.length > 1 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Equity curve</CardTitle>
                  <CardDescription className="text-xs">
                    Daily equity in $. Compares to SPY buy-and-hold (dashed line at final value).
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <PortfolioEquityChart points={status.equity_curve} benchmarkFinal={
                    status.benchmark_dollars !== null ? status.initial_equity + status.benchmark_dollars : null
                  } />
                </CardContent>
              </Card>
            )}

            {/* Open positions at sim end */}
            {status.open_at_end.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Still open at end of window ({status.open_at_end.length})</CardTitle>
                  <CardDescription className="text-xs">
                    Marked at the last available close. Unrealized — not in closed-trade stats above.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-muted-foreground border-b uppercase">
                          <Th>Symbol</Th>
                          <Th>Tier</Th>
                          <Th align="right">Shares</Th>
                          <Th align="right">Entry</Th>
                          <Th align="right">Current</Th>
                          <Th align="right">Unrealized P&amp;L</Th>
                          <Th>Entry date</Th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {status.open_at_end.map((p, i) => (
                          <tr key={i}>
                            <td className="py-1 font-mono font-medium">{p.symbol}</td>
                            <td className="py-1">{p.tier || "—"}</td>
                            <td className="py-1 text-right tabular-nums">{p.shares}</td>
                            <td className="py-1 text-right tabular-nums">{p.entry_price.toFixed(2)}</td>
                            <td className="py-1 text-right tabular-nums">{p.current_price.toFixed(2)}</td>
                            <td className={`py-1 text-right tabular-nums font-medium ${p.unrealized_dollar_pnl >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
                              {fmtDollar(p.unrealized_dollar_pnl)}
                            </td>
                            <td className="py-1">{p.entry_date}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Trade log */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Closed trades (first 500)</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-muted-foreground border-b uppercase">
                        <Th>Symbol</Th>
                        <Th>Tier</Th>
                        <Th>Pattern</Th>
                        <Th>Entry</Th>
                        <Th>Exit</Th>
                        <Th align="right">Shares</Th>
                        <Th align="right">Entry $</Th>
                        <Th align="right">Exit $</Th>
                        <Th align="right">R</Th>
                        <Th align="right">$ P&amp;L</Th>
                        <Th align="right">Bars</Th>
                        <Th>Exit reason</Th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {status.trades.map((t, i) => (
                        <tr key={i}>
                          <td className="py-1 font-mono font-medium">{t.symbol}</td>
                          <td className="py-1 font-bold">{t.tier || "—"}</td>
                          <td className="py-1">{prettyPattern(t.pattern_type)}</td>
                          <td className="py-1 tabular-nums">{t.entry_date}</td>
                          <td className="py-1 tabular-nums">{t.exit_date}</td>
                          <td className="py-1 text-right tabular-nums">{t.shares}</td>
                          <td className="py-1 text-right tabular-nums">{t.entry_price.toFixed(2)}</td>
                          <td className="py-1 text-right tabular-nums">{t.exit_price.toFixed(2)}</td>
                          <td className={`py-1 text-right tabular-nums ${t.r_multiple > 0 ? "text-emerald-600 dark:text-emerald-400" : t.r_multiple < 0 ? "text-destructive" : ""}`}>
                            {t.r_multiple > 0 ? "+" : ""}{t.r_multiple.toFixed(2)}R
                          </td>
                          <td className={`py-1 text-right tabular-nums font-medium ${t.dollar_pnl > 0 ? "text-emerald-600 dark:text-emerald-400" : t.dollar_pnl < 0 ? "text-destructive" : ""}`}>
                            {fmtDollar(t.dollar_pnl)}
                          </td>
                          <td className="py-1 text-right tabular-nums">{t.bars_held}</td>
                          <td className="py-1 capitalize">{t.exit_reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: "green" | "amber" | "red" }) {
  const cls = color === "green" ? "text-emerald-600 dark:text-emerald-400"
    : color === "red" ? "text-destructive"
    : color === "amber" ? "text-amber-600 dark:text-amber-400" : "";
  return (
    <div>
      <div className="text-muted-foreground text-xs uppercase tracking-wide">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${cls}`}>{value}</div>
      {sub && <div className="text-muted-foreground text-xs">{sub}</div>}
    </div>
  );
}

function PortfolioEquityChart({ points, benchmarkFinal }: { points: EquityPoint[]; benchmarkFinal: number | null }) {
  if (points.length < 2) return null;
  const vals = points.map(p => p.equity);
  const initial = points[0]?.equity ?? 0;
  const min = Math.min(...vals, benchmarkFinal ?? initial, initial);
  const max = Math.max(...vals, benchmarkFinal ?? initial, initial);
  const range = max - min || 1;
  const H = 220;
  const W = 100;
  const pl = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W;
    const y = H - ((v - min) / range) * H;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const initialY = H - ((initial - min) / range) * H;
  const benchY = benchmarkFinal !== null ? H - ((benchmarkFinal - min) / range) * H : null;
  const finalV = vals[vals.length - 1];
  const color = finalV >= initial ? "#16a34a" : "#dc2626";

  return (
    <div className="bg-muted/20 w-full overflow-hidden rounded-md p-2">
      <svg viewBox={`0 0 100 ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: H }}>
        <line x1="0" y1={initialY} x2="100" y2={initialY} stroke="#9ca3af" strokeWidth="0.5" strokeDasharray="2,2" />
        {benchY !== null && (
          <line x1="0" y1={benchY} x2="100" y2={benchY} stroke="#f59e0b" strokeWidth="0.8" strokeDasharray="3,3" />
        )}
        <polyline points={pl} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="text-muted-foreground mt-1 flex justify-between text-xs">
        <span>{points[0]?.date}</span>
        <span className="inline-flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <span className="inline-block w-3 border-t-2" style={{ borderColor: color }} /> Strategy
          </span>
          {benchY !== null && (
            <span className="inline-flex items-center gap-1">
              <span className="inline-block w-3 border-t border-dashed" style={{ borderColor: "#f59e0b" }} /> SPY
            </span>
          )}
        </span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
    </div>
  );
}

function Th({
  children,
  align = "left",
  tip,
}: {
  children: React.ReactNode;
  align?: "left" | "right" | "center";
  tip?: string;
}) {
  const alignClass = align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left";
  return (
    <th
      className={`pb-2 ${alignClass} ${tip ? "decoration-muted-foreground/40 underline decoration-dotted underline-offset-4 cursor-help" : ""}`}
      title={tip}
    >
      {children}
    </th>
  );
}

function prettyPattern(p: string): string {
  const map: Record<string, string> = {
    high_tight_flag: "HTF",
    ascending_triangle: "Asc Triangle",
    cwh: "Cup w/Handle",
    vcp: "VCP",
    flat_base: "Flat base",
    three_weeks_tight: "3 Weeks Tight",
    bull_flag: "Bull flag",
    none: "—",
  };
  return map[p] ?? p;
}

// ─── Markdown export builders ────────────────────────────────────────────────

function buildSingleRunMarkdown(params: typeof DEFAULT_PARAMS, r: BacktestOut): string {
  const lines: string[] = [];
  lines.push(`## Backtest — Single run (${new Date().toISOString().slice(0, 10)})`);
  lines.push("");
  lines.push(`**Params:** Account $${params.account_size.toLocaleString()} · Risk ${(params.risk_pct * 100).toFixed(2)}% · Stop ${params.stop_atr}× ATR · Target ${params.target_r}R · Time stop ${params.time_stop} bars · Trigger window ${params.trigger_window} · Pattern quality ≥ ${params.pattern_quality_min} · TT ≥ ${params.tt_min} · ${params.lookback_days} lookback days`);
  lines.push("");
  lines.push(`**Strategy P&L:** ${fmtDollarMd(r.total_dollars)} (${r.total_r >= 0 ? "+" : ""}${r.total_r.toFixed(1)}R, ${r.total_trades.toLocaleString()} triggered trades)`);
  if (r.benchmark_return_pct !== null) {
    lines.push(`**SPY buy & hold:** ${fmtDollarMd(r.benchmark_dollars ?? 0)} (${r.benchmark_return_pct >= 0 ? "+" : ""}${r.benchmark_return_pct.toFixed(1)}%, ${r.benchmark_start_date} → ${r.benchmark_end_date})`);
  }
  lines.push(`**Batting average:** ${(r.win_rate * 100).toFixed(1)}% (${r.wins}W / ${r.losses}L · Profit factor ${r.profit_factor.toFixed(2)})`);
  lines.push(`**Avg win / Avg loss:** +${r.avg_winner_r.toFixed(2)}R / ${r.avg_loser_r.toFixed(2)}R · ratio ${r.win_loss_ratio.toFixed(2)}×`);
  lines.push(`**Max drawdown:** ${r.max_drawdown_r.toFixed(1)}R / $${r.max_drawdown_dollars.toLocaleString("en-US", { maximumFractionDigits: 0 })}`);
  lines.push(`**Frequency:** ${r.trades_per_month.toFixed(1)} trades/mo, ${r.signals_per_month.toFixed(1)} signals/mo`);
  lines.push(`**Universe:** ${r.symbols_scanned.toLocaleString()} symbols scanned, ${r.signals_found.toLocaleString()} signals found`);
  lines.push("");

  if (r.by_tier.length > 0) {
    lines.push(`### Performance by tier`);
    lines.push(`| Tier | Signals | Trigger | Days to fill | Batting | Avg win | Avg loss | W/L ratio | Avg R | EV/signal | Total R | Total $ | Targets | Stops |`);
    lines.push(`|------|--------:|--------:|-------------:|--------:|--------:|---------:|----------:|------:|----------:|--------:|--------:|--------:|------:|`);
    for (const t of r.by_tier) {
      if (t.signals === 0) continue;
      lines.push(`| ${t.tier} | ${t.signals.toLocaleString()} | ${(t.trigger_rate * 100).toFixed(0)}% | ${t.avg_days_to_trigger.toFixed(0)} | ${(t.win_rate * 100).toFixed(0)}% | +${t.avg_winner_r.toFixed(2)}R | ${t.avg_loser_r.toFixed(2)}R | ${t.win_loss_ratio.toFixed(2)}× | ${t.avg_r >= 0 ? "+" : ""}${t.avg_r.toFixed(2)}R | ${t.expectancy_per_signal_r >= 0 ? "+" : ""}${t.expectancy_per_signal_r.toFixed(2)}R | ${t.total_r >= 0 ? "+" : ""}${t.total_r.toFixed(1)}R | ${fmtDollarMd(t.total_dollars)} | ${t.target_hits} | ${t.stop_hits} |`);
    }
    lines.push("");
  }

  if (r.by_pattern.length > 0) {
    lines.push(`### Performance by pattern`);
    lines.push(`| Pattern | Signals | Trigger | Batting | Avg win | Avg loss | W/L ratio | Avg R | Total R | Total $ |`);
    lines.push(`|---------|--------:|--------:|--------:|--------:|---------:|----------:|------:|--------:|--------:|`);
    for (const p of r.by_pattern) {
      lines.push(`| ${prettyPattern(p.pattern_type)} | ${p.signals.toLocaleString()} | ${(p.trigger_rate * 100).toFixed(0)}% | ${(p.win_rate * 100).toFixed(0)}% | +${p.avg_winner_r.toFixed(2)}R | ${p.avg_loser_r.toFixed(2)}R | ${p.win_loss_ratio.toFixed(2)}× | ${p.avg_r >= 0 ? "+" : ""}${p.avg_r.toFixed(2)}R | ${p.total_r >= 0 ? "+" : ""}${p.total_r.toFixed(1)}R | ${fmtDollarMd(p.total_dollars)} |`);
    }
    lines.push("");
  }

  lines.push(`_Note: Total $ assumes infinite capital — see Portfolio sim tab for realistic equity simulation._`);
  return lines.join("\n");
}

function buildSweepMarkdown(sweep: SweepOut, baseParams: typeof DEFAULT_PARAMS): string {
  const preset = SWEEP_PRESETS[sweep.sweep_param];
  const lines: string[] = [];
  lines.push(`## Backtest — Parameter sweep (${new Date().toISOString().slice(0, 10)})`);
  lines.push("");
  lines.push(`**Swept parameter:** ${preset?.label ?? sweep.sweep_param}`);
  lines.push(`**Held constant:** Account $${baseParams.account_size.toLocaleString()} · Risk ${(baseParams.risk_pct * 100).toFixed(2)}% · Stop ${baseParams.stop_atr}× ATR · Target ${baseParams.target_r}R · Time stop ${baseParams.time_stop} · Trigger window ${baseParams.trigger_window} · Pattern q ≥ ${baseParams.pattern_quality_min} · TT ≥ ${baseParams.tt_min}`);
  lines.push("");
  lines.push(`| ${preset?.label.split("(")[0].trim() ?? sweep.sweep_param} | Signals | Trades | Batting | Avg win | Avg loss | W/L ratio | Avg R | Profit factor | Total R | Total $ |`);
  lines.push(`|---:|--------:|-------:|--------:|--------:|---------:|----------:|------:|--------------:|--------:|--------:|`);
  const bestIdx = sweep.rows.length > 0 ? sweep.rows.reduce((b, r, i, a) => r.avg_r > a[b].avg_r ? i : b, 0) : -1;
  const isInt = preset?.integer ?? false;
  const unit = preset?.unit ?? "";
  for (let i = 0; i < sweep.rows.length; i++) {
    const r = sweep.rows[i];
    const valStr = (isInt ? r.value.toFixed(0) : r.value.toFixed(2)) + unit;
    const marker = i === bestIdx ? " ★" : "";
    lines.push(`| ${valStr}${marker} | ${r.signals_found.toLocaleString()} | ${r.total_trades.toLocaleString()} | ${(r.win_rate * 100).toFixed(0)}% | +${r.avg_winner_r.toFixed(2)}R | ${r.avg_loser_r.toFixed(2)}R | ${r.win_loss_ratio.toFixed(2)}× | ${r.avg_r >= 0 ? "+" : ""}${r.avg_r.toFixed(2)}R | ${r.profit_factor.toFixed(2)} | ${r.total_r >= 0 ? "+" : ""}${r.total_r.toFixed(1)}R | ${fmtDollarMd(r.total_dollars)} |`);
  }
  lines.push("");
  lines.push(`★ = best row by Avg R per trade`);
  return lines.join("\n");
}

function buildPortfolioMarkdown(params: typeof DEFAULT_PARAMS, portfolioParams: { maxConcurrent: number; maxOpenRisk: number; cooldownBars: number }, r: PortfolioOut): string {
  const lines: string[] = [];
  lines.push(`## Backtest — Portfolio simulation (${new Date().toISOString().slice(0, 10)})`);
  lines.push("");
  lines.push(`**Signal params:** Stop ${params.stop_atr}× ATR · Target ${params.target_r}R · Time stop ${params.time_stop} · Trigger window ${params.trigger_window} · Pattern q ≥ ${params.pattern_quality_min} · TT ≥ ${params.tt_min}`);
  lines.push(`**Portfolio params:** Account $${r.initial_equity.toLocaleString()} · Risk ${(params.risk_pct * 100).toFixed(2)}%/trade · Max ${portfolioParams.maxConcurrent} concurrent · Max ${(portfolioParams.maxOpenRisk * 100).toFixed(1)}% total open risk · ${portfolioParams.cooldownBars}-bar cooldown after exit`);
  lines.push("");
  lines.push(`### Outcome`);
  lines.push(`- **Final equity:** $${r.final_equity.toLocaleString("en-US", { maximumFractionDigits: 0 })} (started $${r.initial_equity.toLocaleString("en-US", { maximumFractionDigits: 0 })})`);
  lines.push(`- **Total return:** ${r.total_return_pct >= 0 ? "+" : ""}${r.total_return_pct.toFixed(2)}%`);
  lines.push(`- **CAGR:** ${r.cagr_pct >= 0 ? "+" : ""}${r.cagr_pct.toFixed(2)}%`);
  lines.push(`- **Max drawdown:** -${r.max_drawdown_pct.toFixed(2)}% (-$${r.max_drawdown_dollars.toLocaleString("en-US", { maximumFractionDigits: 0 })})`);
  if (r.benchmark_return_pct !== null) {
    lines.push(`- **SPY benchmark:** ${r.benchmark_return_pct >= 0 ? "+" : ""}${r.benchmark_return_pct.toFixed(2)}% over same window (${r.benchmark_start_date} → ${r.benchmark_end_date})`);
  }
  lines.push(`- **Time in market:** ${r.time_in_market_pct.toFixed(1)}%`);
  lines.push(`- **Concurrent positions:** avg ${r.avg_concurrent_positions.toFixed(1)} / max ${r.max_concurrent_positions}`);
  lines.push("");
  lines.push(`### Trade stats`);
  lines.push(`- **Closed trades:** ${r.closed_trades.toLocaleString()} (${r.wins}W / ${r.losses}L)`);
  lines.push(`- **Batting average:** ${(r.win_rate * 100).toFixed(1)}% · Profit factor ${r.profit_factor.toFixed(2)}`);
  lines.push(`- **Avg win / Avg loss:** +${r.avg_winner_r.toFixed(2)}R / ${r.avg_loser_r.toFixed(2)}R · ratio ${r.win_loss_ratio.toFixed(2)}×`);
  lines.push(`- **Avg R per trade:** ${r.avg_r >= 0 ? "+" : ""}${r.avg_r.toFixed(2)}R`);
  lines.push("");
  lines.push(`### Signal acceptance`);
  lines.push(`- Considered: ${r.total_signals_considered.toLocaleString()}`);
  lines.push(`- Triggered (pivot hit): ${r.total_signals_triggered.toLocaleString()}`);
  lines.push(`- **Taken: ${r.total_signals_taken.toLocaleString()} (${(r.signal_acceptance_rate * 100).toFixed(1)}% of triggered)**`);
  lines.push(`- Skipped — already in symbol: ${r.rejected_already_open.toLocaleString()}`);
  lines.push(`- Skipped — capital full: ${r.rejected_capital.toLocaleString()}`);
  lines.push(`- Skipped — cooldown: ${r.rejected_cooldown.toLocaleString()}`);
  if (r.open_at_end.length > 0) {
    lines.push("");
    lines.push(`### Still open at end (${r.open_at_end.length})`);
    lines.push(`| Symbol | Tier | Shares | Entry | Current | Unrealized $ | Entry date |`);
    lines.push(`|--------|------|-------:|------:|--------:|-------------:|------------|`);
    for (const p of r.open_at_end) {
      lines.push(`| ${p.symbol} | ${p.tier || "—"} | ${p.shares} | ${p.entry_price.toFixed(2)} | ${p.current_price.toFixed(2)} | ${fmtDollarMd(p.unrealized_dollar_pnl)} | ${p.entry_date} |`);
    }
  }
  return lines.join("\n");
}

function fmtDollarMd(n: number): string {
  const sign = n >= 0 ? "+" : "−";
  const abs = Math.abs(n);
  return `${sign}$${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  const cls = color === "green" ? "text-emerald-600 dark:text-emerald-400"
    : color === "red" ? "text-destructive"
    : color === "amber" ? "text-amber-600 dark:text-amber-400" : "";
  return (
    <Card>
      <CardContent className="pt-4 pb-3">
        <div className="text-muted-foreground text-xs uppercase tracking-wide">{label}</div>
        <div className={`text-xl font-bold tabular-nums ${cls}`}>{value}</div>
        {sub && <div className="text-muted-foreground text-xs mt-1">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function EquityMiniChart({
  curve,
  benchmarkFinal,
}: {
  curve: Array<{ cumulative_dollars: number }>;
  benchmarkFinal: number | null;
}) {
  if (curve.length < 2) return null;
  const vals = curve.map(p => p.cumulative_dollars);
  const min  = Math.min(...vals, 0, benchmarkFinal ?? 0);
  const max  = Math.max(...vals, 0, benchmarkFinal ?? 0);
  const range = max - min || 1;
  const H = 160;
  const W = 100;
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W;
    const y = H - ((v - min) / range) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const zeroY = H - ((0 - min) / range) * H;
  const finalV = vals[vals.length - 1];
  const color  = finalV >= 0 ? "#16a34a" : "#dc2626";

  let benchY: number | null = null;
  if (benchmarkFinal !== null) {
    // Draw a horizontal dashed line at SPY's final value — flat reference.
    benchY = H - ((benchmarkFinal - min) / range) * H;
  }

  return (
    <div className="bg-muted/20 w-full overflow-hidden rounded-md p-2">
      <svg viewBox={`0 0 100 ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: H }}>
        <line x1="0" y1={zeroY} x2="100" y2={zeroY} stroke="#9ca3af" strokeWidth="0.5" strokeDasharray="2,2" />
        {benchY !== null && (
          <line x1="0" y1={benchY} x2="100" y2={benchY} stroke="#f59e0b" strokeWidth="0.8" strokeDasharray="3,3" />
        )}
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="text-muted-foreground mt-1 flex justify-between text-xs">
        <span className="inline-flex items-center gap-2">
          <span className="inline-block w-3 border-t-2" style={{ borderColor: color }} /> Strategy
        </span>
        {benchmarkFinal !== null && (
          <span className="inline-flex items-center gap-2">
            <span className="inline-block w-3 border-t border-dashed" style={{ borderColor: "#f59e0b" }} /> SPY buy & hold
          </span>
        )}
      </div>
    </div>
  );
}
