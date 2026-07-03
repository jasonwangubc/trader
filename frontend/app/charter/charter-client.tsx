"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { API_URL } from "@/lib/api";

// ── Types ────────────────────────────────────────────────────────────────────

interface Charter {
  id: string;
  version: number;
  content_md: string;
  rules: Record<string, unknown>;
  note: string | null;
  created_at: string;
}

interface PerfPoint {
  date: string;
  counterfactual: number | null;
  actual: number | null;
}

interface CurrencyPerf {
  currency: string;
  benchmark_symbol: string;
  deposits_total: number;
  withdrawals_total: number;
  flow_count: number;
  points: PerfPoint[];
  monthly: { month: string; actual_end: number | null; counterfactual_end: number | null }[];
  actual_max_drawdown_pct: number | null;
  latest_actual: number | null;
  latest_counterfactual: number | null;
  status: "ok" | "lagging" | "insufficient_history";
  status_detail: string;
}

const ACTUAL_COLOR = "#059669";      // emerald — your account
const BENCHMARK_COLOR = "#64748b";   // slate — the neutral reference line (also dashed)

function fmtMoney(v: number | null | undefined, ccy: string): string {
  if (v === null || v === undefined) return "—";
  return `${ccy} ${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

// ── Performance section ──────────────────────────────────────────────────────

export function CharterPerformance() {
  const [data, setData] = useState<CurrencyPerf[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API_URL}/api/charter/performance`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d: { currencies: CurrencyPerf[] }) => setData(d.currencies))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  if (error) return <p className="text-destructive text-sm">{error}</p>;
  if (!data) return <div className="h-52 animate-pulse rounded-lg bg-muted/20" />;
  if (data.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No cash flows or equity snapshots yet. Run &ldquo;Sync broker history&rdquo; on the
        journal page to backfill ~2 years of deposits, and sync your accounts once so
        equity snapshots start accruing.
      </p>
    );
  }

  return (
    <div className="space-y-8">
      {data.map((perf) => (
        <CurrencyBlock key={perf.currency} perf={perf} />
      ))}
    </div>
  );
}

function StatusBanner({ perf }: { perf: CurrencyPerf }) {
  const style =
    perf.status === "ok"
      ? "border-emerald-300 bg-emerald-50 dark:bg-emerald-950/20 text-emerald-700 dark:text-emerald-400"
      : perf.status === "lagging"
      ? "border-amber-300 bg-amber-50 dark:bg-amber-950/20 text-amber-700 dark:text-amber-400"
      : "border-muted bg-muted/20 text-muted-foreground";
  const label =
    perf.status === "ok"
      ? "Beating the benchmark"
      : perf.status === "lagging"
      ? "Lagging the benchmark — kill/scale criteria apply"
      : "Not enough history for a verdict yet";
  return (
    <div className={`rounded-lg border px-3 py-2 text-sm ${style}`}>
      <span className="font-semibold">{label}.</span>{" "}
      <span className="opacity-90">{perf.status_detail}</span>
    </div>
  );
}

function CurrencyBlock({ perf }: { perf: CurrencyPerf }) {
  const chartData = perf.points.map((p) => ({
    date: p.date,
    Account: p.actual,
    Benchmark: p.counterfactual,
  }));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">
          {perf.currency} · vs {perf.benchmark_symbol || "no benchmark"}
        </h3>
        <div className="text-xs text-muted-foreground">
          {perf.flow_count} cash flows · deposits {fmtMoney(perf.deposits_total, perf.currency)}
          {perf.withdrawals_total !== 0 && <> · withdrawals {fmtMoney(perf.withdrawals_total, perf.currency)}</>}
          {perf.actual_max_drawdown_pct !== null && perf.actual_max_drawdown_pct > 0 && (
            <> · max drawdown −{(perf.actual_max_drawdown_pct * 100).toFixed(1)}%</>
          )}
        </div>
      </div>

      <StatusBanner perf={perf} />

      {/* Latest values */}
      <div className="flex gap-8 text-sm">
        <div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="inline-block h-0.5 w-4" style={{ background: ACTUAL_COLOR }} />
            Your account
          </div>
          <div className="font-semibold tabular-nums">{fmtMoney(perf.latest_actual, perf.currency)}</div>
        </div>
        <div>
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="inline-block w-4 border-t-2 border-dashed" style={{ borderColor: BENCHMARK_COLOR }} />
            If indexed ({perf.benchmark_symbol})
          </div>
          <div className="font-semibold tabular-nums">{fmtMoney(perf.latest_counterfactual, perf.currency)}</div>
        </div>
      </div>

      {chartData.length > 1 && (
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: 12 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.08} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11 }}
                tickLine={false}
                minTickGap={60}
                stroke="currentColor"
                opacity={0.5}
              />
              <YAxis
                tick={{ fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={70}
                stroke="currentColor"
                opacity={0.5}
                tickFormatter={(v: number) => v.toLocaleString(undefined, { notation: "compact" })}
              />
              <Tooltip
                formatter={(value) =>
                  typeof value === "number"
                    ? value.toLocaleString(undefined, { maximumFractionDigits: 0 })
                    : "—"
                }
                contentStyle={{ fontSize: 12 }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="Benchmark"
                stroke={BENCHMARK_COLOR}
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="Account"
                stroke={ACTUAL_COLOR}
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Monthly table */}
      {perf.monthly.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Monthly table ({perf.monthly.length} months)
          </summary>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-muted-foreground border-b">
                  <th className="py-1 text-left font-medium">Month</th>
                  <th className="py-1 text-right font-medium">Your account</th>
                  <th className="py-1 text-right font-medium">If indexed</th>
                </tr>
              </thead>
              <tbody>
                {perf.monthly.map((m) => (
                  <tr key={m.month} className="border-b border-border/40">
                    <td className="py-1">{m.month}</td>
                    <td className="py-1 text-right tabular-nums">{fmtMoney(m.actual_end, perf.currency)}</td>
                    <td className="py-1 text-right tabular-nums">{fmtMoney(m.counterfactual_end, perf.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}

// ── Charter editor + versions ────────────────────────────────────────────────

const CHARTER_TEMPLATE = `# Trading Charter

## The experiment
- Account: RRSP only (~$50k). No other account trades this system.
- Horizon: 2 years, evaluated quarterly against the benchmark below.
- Benchmark: buy-and-hold ZSP.TO (CAD) / SPY (USD) with the same deposits.

## Risk rules (locked)
- Risk per trade: 0.75% base, anti-martingale streak scaling, 2% hard cap.
- Every position has a stop before entry. The stop is honored 100% of the time.
- Drawdown breaker: warn -10% from peak, half-size -12.5%, no new entries -15%.
- Mostly cash when the market regime is bearish (SPY below 200-day average).

## Kill / scale criteria (pre-committed)
- If trailing performance lags the benchmark after 12+ months: halve position sizes.
- If the 2-year verdict is "no edge": index the money, keep the wheel income only.
- If the account hits -20% from starting equity: stop, full written review before
  any new trade.

## Why this exists
Written while calm. The version history of this document is the proof that these
rules predate whatever drawdown tempts me to bend them.
`;

export function CharterEditor() {
  const [active, setActive] = useState<Charter | null | undefined>(undefined);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/charter`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d: Charter | null) => setActive(d))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  useEffect(load, [load]);

  const publish = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/charter`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content_md: text,
          note: active ? note || null : note || "initial version",
        }),
      });
      if (!res.ok) {
        const d = (await res.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(typeof d?.detail === "string" ? d.detail : res.statusText);
      }
      setEditing(false);
      setNote("");
      load();
      // Refresh the versions list below.
      window.dispatchEvent(new Event("charter-published"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  if (active === undefined) return <div className="h-24 animate-pulse rounded-lg bg-muted/20" />;

  return (
    <div className="space-y-3">
      {active && !editing && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              Version {active.version} · published {active.created_at.slice(0, 10)}
              {active.note && <> · &ldquo;{active.note}&rdquo;</>}
            </span>
            <button
              onClick={() => { setText(active.content_md); setEditing(true); }}
              className="border-input hover:bg-muted rounded-md border px-3 py-1 text-xs"
            >
              Draft new version
            </button>
          </div>
          <pre className="whitespace-pre-wrap rounded-lg border bg-muted/20 p-4 text-sm leading-relaxed font-sans">
            {active.content_md}
          </pre>
        </>
      )}

      {!active && !editing && (
        <div className="rounded-lg border border-dashed p-6 text-center">
          <p className="text-sm text-muted-foreground mb-3">
            No charter yet. Write your rules now, while you&apos;re calm — the whole point
            is that they exist before the first drawdown tests them.
          </p>
          <button
            onClick={() => { setText(CHARTER_TEMPLATE); setEditing(true); }}
            className="bg-primary text-primary-foreground rounded-md px-4 py-2 text-sm font-medium hover:bg-primary/90"
          >
            Start from template
          </button>
        </div>
      )}

      {editing && (
        <div className="space-y-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={20}
            className="border-input bg-background w-full rounded-md border p-3 font-mono text-sm"
          />
          {active && (
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Why are you revising the charter? (required — this is audited)"
              className="border-input bg-background h-9 w-full rounded-md border px-3 text-sm"
            />
          )}
          <div className="flex gap-2">
            <button
              onClick={publish}
              disabled={saving || text.trim().length < 50 || (!!active && note.trim().length === 0)}
              className="bg-primary text-primary-foreground rounded-md px-4 py-2 text-sm font-medium hover:bg-primary/90 disabled:opacity-50"
            >
              {saving ? "Publishing…" : active ? `Publish version ${active.version + 1}` : "Publish version 1"}
            </button>
            <button
              onClick={() => setEditing(false)}
              className="border-input hover:bg-muted rounded-md border px-4 py-2 text-sm"
            >
              Cancel
            </button>
          </div>
          {error && <p className="text-destructive text-xs">{error}</p>}
        </div>
      )}
    </div>
  );
}

export function CharterVersions() {
  const [versions, setVersions] = useState<Charter[]>([]);

  const load = useCallback(() => {
    fetch(`${API_URL}/api/charter/versions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then(setVersions)
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("charter-published", load);
    return () => window.removeEventListener("charter-published", load);
  }, [load]);

  if (versions.length <= 1) return null;

  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground mb-2">Version history (immutable)</p>
      <ol className="space-y-1.5 text-sm">
        {versions.map((v) => (
          <li key={v.id} className="flex items-baseline gap-2">
            <span className="font-mono text-xs text-muted-foreground shrink-0">
              v{v.version} · {v.created_at.slice(0, 10)}
            </span>
            <span className="text-muted-foreground text-xs truncate">
              {v.note ?? "—"}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
