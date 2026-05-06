"use client";

import { useState, useEffect } from "react";
import { Play, RefreshCw } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_URL } from "@/lib/api";

interface BacktestOut {
  status: "idle" | "running" | "done";
  total: number;
  wins: number;
  losses: number;
  scratches: number;
  win_rate: number;
  avg_r: number;
  avg_winner_r: number;
  avg_loser_r: number;
  expectancy: number;
  profit_factor: number;
  total_r: number;
  max_drawdown_r: number;
  symbols_scanned: number;
  signals_found: number;
  equity_curve: Array<{ date: string; symbol: string; r: number; cumulative_r: number }>;
  trades: Array<{
    symbol: string; entry_date: string; exit_date: string;
    entry_price: number; exit_price: number; exit_reason: string;
    r_multiple: number; tt_score: number; vcp_score: number; bars_held: number;
  }>;
}

const DEFAULT_PARAMS = {
  tt_min: 6,
  vcp_min: 0.5,
  stop_atr: 1.5,
  target_r: 3.0,
  time_stop: 20,
  lookback_days: 504,
};

export default function BacktestPage() {
  const [params, setParams]   = useState(DEFAULT_PARAMS);
  const [result, setResult]   = useState<BacktestOut | null>(null);
  const [running, setRunning] = useState(false);

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
      body: JSON.stringify(params),
    });
    setTimeout(poll, 2000);
  };

  const set = (k: string, v: string) =>
    setParams(p => ({ ...p, [k]: parseFloat(v) || parseInt(v) || 0 }));

  return (
    <main className="container mx-auto max-w-6xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Backtest</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Walk-forward simulation: finds every session where TT + VCP exceeded your thresholds,
          simulates entry at next open, exits at stop / target / time stop.
        </p>
      </header>

      <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
        {/* Params */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Parameters</CardTitle>
              <CardDescription className="text-xs">
                Uses the 2yr of daily bars already in your DB.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {[
                { key: "tt_min",        label: "Min Trend Template (0-8)", step: "1",   min: "1", max: "8" },
                { key: "vcp_min",       label: "Min VCP score (0-1)",      step: "0.1", min: "0", max: "1" },
                { key: "stop_atr",      label: "Stop (× ATR-14)",          step: "0.25",min: "0.5",max:"5" },
                { key: "target_r",      label: "Target (× risk = R)",      step: "0.5", min: "1", max: "10" },
                { key: "time_stop",     label: "Time stop (bars)",         step: "1",   min: "5", max: "60" },
                { key: "lookback_days", label: "Lookback (trading days)",  step: "63",  min: "126",max:"504"},
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
                  Scanned {result.symbols_scanned} symbols · {result.signals_found} signals found
                </p>
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
                Simulating trades across the full universe… this takes 1–3 minutes.
              </CardContent>
            </Card>
          )}

          {!running && result?.status === "done" && result.total > 0 && (
            <>
              {/* Summary stats */}
              <div className="grid grid-cols-4 gap-3">
                <StatCard label="Trades" value={String(result.total)} />
                <StatCard
                  label="Win rate"
                  value={`${(result.win_rate * 100).toFixed(0)}%`}
                  sub={`${result.wins}W / ${result.losses}L`}
                  color={result.win_rate >= 0.5 ? "green" : result.win_rate >= 0.35 ? "amber" : "red"}
                />
                <StatCard
                  label="Expectancy"
                  value={`${result.expectancy > 0 ? "+" : ""}${result.expectancy.toFixed(2)}R`}
                  color={result.expectancy > 0 ? "green" : "red"}
                />
                <StatCard
                  label="Profit factor"
                  value={result.profit_factor === Infinity ? "∞" : result.profit_factor.toFixed(2)}
                  color={result.profit_factor >= 1.5 ? "green" : result.profit_factor >= 1 ? "amber" : "red"}
                />
                <StatCard label="Avg winner" value={`+${result.avg_winner_r.toFixed(2)}R`} color="green" />
                <StatCard label="Avg loser" value={`${result.avg_loser_r.toFixed(2)}R`} color="red" />
                <StatCard
                  label="Total R"
                  value={`${result.total_r > 0 ? "+" : ""}${result.total_r.toFixed(1)}R`}
                  color={result.total_r > 0 ? "green" : "red"}
                />
                <StatCard label="Max drawdown" value={`-${result.max_drawdown_r.toFixed(1)}R`} color="red" />
              </div>

              {/* Equity curve */}
              {result.equity_curve.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Simulated equity curve</CardTitle>
                    <CardDescription className="text-xs">
                      Each point = one simulated trade. Cumulative R if you took every signal.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <EquityMiniChart curve={result.equity_curve} />
                  </CardContent>
                </Card>
              )}

              {/* Trade log */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Trade log (first 200)</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-muted-foreground border-b uppercase">
                          <th className="pb-2 text-left">Symbol</th>
                          <th className="pb-2 text-left">Entry</th>
                          <th className="pb-2 text-left">Exit</th>
                          <th className="pb-2 text-right">R</th>
                          <th className="pb-2 text-right">Reason</th>
                          <th className="pb-2 text-right">TT</th>
                          <th className="pb-2 text-right">VCP</th>
                          <th className="pb-2 text-right">Bars</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y">
                        {result.trades.map((t, i) => (
                          <tr key={i}>
                            <td className="py-1 font-mono font-medium">{t.symbol}</td>
                            <td className="py-1 tabular-nums">{t.entry_date}</td>
                            <td className="py-1 tabular-nums">{t.exit_date}</td>
                            <td className={`py-1 text-right tabular-nums font-medium ${
                              t.r_multiple > 0.1 ? "text-emerald-600 dark:text-emerald-400" :
                              t.r_multiple < -0.05 ? "text-destructive" : ""
                            }`}>
                              {t.r_multiple > 0 ? "+" : ""}{t.r_multiple.toFixed(2)}R
                            </td>
                            <td className="py-1 text-right capitalize">{t.exit_reason}</td>
                            <td className="py-1 text-right tabular-nums">{t.tt_score}/8</td>
                            <td className="py-1 text-right tabular-nums">{(t.vcp_score * 10).toFixed(1)}</td>
                            <td className="py-1 text-right tabular-nums">{t.bars_held}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>
            </>
          )}

          {!running && result?.status === "done" && result.total === 0 && (
            <Card>
              <CardContent className="py-12 text-center text-sm text-muted-foreground">
                No signals found with these parameters. Try lowering TT min or VCP min,
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
    </main>
  );
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
        {sub && <div className="text-muted-foreground text-xs">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function EquityMiniChart({ curve }: { curve: Array<{ cumulative_r: number }> }) {
  if (curve.length < 2) return null;
  const vals = curve.map(p => p.cumulative_r);
  const min  = Math.min(...vals, 0);
  const max  = Math.max(...vals, 0);
  const range = max - min || 1;
  const H = 120;
  const W = 100;
  const points = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * W;
    const y = H - ((v - min) / range) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const zeroY = H - ((0 - min) / range) * H;
  const finalR = vals[vals.length - 1];
  const color  = finalR >= 0 ? "#16a34a" : "#dc2626";

  return (
    <div className="w-full overflow-hidden rounded-md bg-muted/20 p-2">
      <svg viewBox={`0 0 100 ${H}`} preserveAspectRatio="none" className="w-full" style={{ height: H }}>
        <line x1="0" y1={zeroY} x2="100" y2={zeroY} stroke="#9ca3af" strokeWidth="0.5" strokeDasharray="2,2" />
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="mt-1 flex justify-between text-xs text-muted-foreground">
        <span>{curve[0]?.date ?? ""}</span>
        <span className={finalR >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}>
          {finalR >= 0 ? "+" : ""}{finalR.toFixed(1)}R total
        </span>
        <span>{curve[curve.length - 1]?.date ?? ""}</span>
      </div>
    </div>
  );
}
