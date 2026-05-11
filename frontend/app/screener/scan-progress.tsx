"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import { API_URL } from "@/lib/api";

interface ScanProgress {
  stage: string;
  stage_label: string;
  stage_index: number;
  total_stages: number;
  processed: number;
  total: number;
  pct: number;
  started_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  error: string | null;
}

interface SyncStatus {
  running: boolean;
  message: string;
  stats: Record<string, number> | null;
  progress: ScanProgress | null;
}

const POLL_MS = 2000;

interface Props {
  initialRunning: boolean;
  initialProgress: ScanProgress | null;
}

/** Live progress bar for screener scans.
 *
 * Polls /api/screener/sync/status every 2s while a scan is running. Shows
 * the current stage, an overall percent, and per-symbol counts within long
 * stages. Triggers a router.refresh() once the scan finishes so the table
 * picks up the freshly-scored rows.
 */
export function ScanProgress({ initialRunning, initialProgress }: Props) {
  const router = useRouter();
  const [running, setRunning]   = useState(initialRunning);
  const [progress, setProgress] = useState<ScanProgress | null>(initialProgress);
  const [, setTick] = useState(0);  // incremented every second to force elapsed re-render
  const wasRunningRef = useRef(initialRunning);
  const [showFinished, setShowFinished] = useState(false);

  // Tick every second while running so the elapsed counter advances live
  // without waiting for the next poll result from the backend.
  useEffect(() => {
    if (!running) return;
    const h = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(h);
  }, [running]);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API_URL}/api/screener/sync/status`, { cache: "no-store" });
        if (!res.ok) return;
        const data: SyncStatus = await res.json();
        if (cancelled) return;
        setRunning(data.running);
        setProgress(data.progress);
        if (!data.running && wasRunningRef.current) {
          // Scan just finished — refresh the page once so results table updates.
          setShowFinished(true);
          router.refresh();
          // Hide the completion banner after a moment.
          setTimeout(() => { if (!cancelled) setShowFinished(false); }, 8000);
        }
        wasRunningRef.current = data.running;
      } catch {
        // network blips are fine — next tick will retry
      }
    };
    const handle = setInterval(poll, POLL_MS);
    // Kick off an immediate poll so the bar updates without a 2s lag
    poll();
    return () => { cancelled = true; clearInterval(handle); };
  }, [running, router]);

  if (!running && !showFinished) return null;
  if (!progress) return null;

  const isError    = progress.stage === "error";
  const isDone     = progress.stage === "done" || (!running && progress.finished_at);
  const pct        = Math.max(0, Math.min(100, progress.pct ?? 0));
  // Use wall-clock time for elapsed so it advances even during batch stages
  // that don't tick (e.g. EOD download). Use finished_at once done.
  const elapsedEnd = isDone ? (progress.finished_at ?? undefined) : undefined;
  const elapsed    = progress.started_at ? elapsedSeconds(progress.started_at, elapsedEnd) : 0;
  const eta        = (running && pct > 2 && pct < 99)
    ? Math.max(0, Math.round((elapsed / (pct / 100)) - elapsed))
    : 0;

  const barColor =
    isError ? "bg-rose-500" :
    isDone  ? "bg-emerald-500" :
              "bg-primary";

  return (
    <div className={`rounded-lg border p-3 ${
      isError ? "border-rose-400/40 bg-rose-500/5" :
      isDone  ? "border-emerald-400/40 bg-emerald-500/5" :
                "border-border/60 bg-card/60"
    }`}>
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          {isError ? (
            <AlertTriangle className="h-4 w-4 shrink-0 text-rose-500" />
          ) : isDone ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
          ) : (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
          )}
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">
              {isError ? "Scan failed"
              : isDone  ? "Scan complete"
              :           progress.stage_label || "Starting…"}
            </div>
            {!isDone && !isError && (
              <div className="text-[11px] text-muted-foreground tabular-nums">
                {progress.total > 0
                  ? `${progress.processed.toLocaleString()} / ${progress.total.toLocaleString()} symbols · `
                  : ""}
                stage {progress.stage_index + 1} of {progress.total_stages}
              </div>
            )}
            {isDone && progress.finished_at && (
              <div className="text-[11px] text-muted-foreground">
                Took {humanDuration(elapsedSeconds(progress.started_at!, progress.finished_at))}
              </div>
            )}
            {isError && progress.error && (
              <div className="text-[11px] text-rose-500/90 truncate">{progress.error}</div>
            )}
          </div>
        </div>
        <div className="text-sm font-semibold tabular-nums shrink-0">
          {pct.toFixed(0)}%
        </div>
      </div>

      {/* Overall progress bar */}
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* ETA + elapsed */}
      {running && !isError && elapsed > 0 && (
        <div className="mt-1.5 flex items-center justify-between text-[11px] text-muted-foreground tabular-nums">
          <span>elapsed {humanDuration(elapsed)}</span>
          {eta > 0 && <span>~{humanDuration(eta)} remaining</span>}
        </div>
      )}
    </div>
  );
}

function elapsedSeconds(startIso: string, endIso?: string | null): number {
  const start = new Date(startIso).getTime();
  // endIso omitted or null → use current wall-clock time (live scan)
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  return Math.max(0, Math.round((end - start) / 1000));
}

function humanDuration(secs: number): string {
  secs = Math.round(secs);
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm > 0 ? `${h}h ${mm}m` : `${h}h`;
}
