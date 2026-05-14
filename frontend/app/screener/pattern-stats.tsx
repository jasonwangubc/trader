"use client";

import { useEffect, useState } from "react";
import { API_URL } from "@/lib/api";

interface PatternStatRow {
  pattern_type: string;
  label: string;
  count: number;
  avg_quality: number;
  win_rate: string;
  avg_gain: string;
}

export function PatternStats() {
  const [rows, setRows] = useState<PatternStatRow[]>([]);

  useEffect(() => {
    fetch(`${API_URL}/api/screener/pattern-stats`)
      .then(r => r.json())
      .then((data: PatternStatRow[]) => {
        // Sort by count descending, then by win_rate string
        setRows([...data].sort((a, b) => b.count - a.count));
      })
      .catch(() => {});
  }, []);

  if (rows.length === 0) return null;

  return (
    <div className="rounded-lg border border-border/60 bg-card/50 overflow-hidden">
      <div className="px-3 py-2 border-b border-border/40">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Pattern breakdown — today's scan
        </h3>
        <p className="text-[11px] text-muted-foreground/70 mt-0.5">
          Win rates from Bulkowski / practitioner consensus — not backtested on this universe.
        </p>
      </div>
      <table className="w-full text-xs">
        <thead className="bg-muted/20 border-b border-border/40">
          <tr>
            <th className="px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wide text-muted-foreground/70">Pattern</th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wide text-muted-foreground/70">Hits</th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wide text-muted-foreground/70">Avg quality</th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wide text-muted-foreground/70">Win rate*</th>
            <th className="px-3 py-1.5 text-right text-[10px] font-medium uppercase tracking-wide text-muted-foreground/70">Avg gain*</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.pattern_type} className="border-b border-border/20 last:border-0 hover:bg-muted/10">
              <td className="px-3 py-1.5 font-medium">{r.label}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {r.count > 0 ? (
                  <span className="font-semibold">{r.count.toLocaleString()}</span>
                ) : (
                  <span className="text-muted-foreground/50">—</span>
                )}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {r.count > 0 ? (
                  <span className={r.avg_quality >= 65 ? "text-emerald-400" : r.avg_quality >= 45 ? "text-amber-400" : "text-muted-foreground"}>
                    {r.avg_quality.toFixed(0)}/100
                  </span>
                ) : (
                  <span className="text-muted-foreground/50">—</span>
                )}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{r.win_rate}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{r.avg_gain}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
