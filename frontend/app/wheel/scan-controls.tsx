"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { API_URL } from "@/lib/api";
import type { ScanStatus } from "@/lib/wheel";

export function ScanControls({
  initialStatus,
  hasCandidates,
}: {
  initialStatus: ScanStatus;
  hasCandidates: boolean;
}) {
  const router = useRouter();
  const [status, setStatus] = useState<ScanStatus>(initialStatus);
  const [running, setRunning] = useState(initialStatus.running);
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState({
    target_dte: 30,
    min_annualized_yield: 0.10,
    max_annualized_yield: 0.50,
    target_csp_otm_pct: 0.07,
    target_cc_otm_pct: 0.05,
    min_open_interest: 50,
    max_candidates_to_scan: 60,
    max_implied_volatility: 0.55,
  });

  const pollRef = useRef<number | null>(null);
  const startPolling = () => {
    if (pollRef.current !== null) return;
    pollRef.current = window.setInterval(async () => {
      try {
        const res = await fetch(`${API_URL}/api/wheel/scan/status`, { cache: "no-store" });
        const s: ScanStatus = await res.json();
        setStatus(s);
        if (!s.running) {
          window.clearInterval(pollRef.current!);
          pollRef.current = null;
          setRunning(false);
          router.refresh();
        }
      } catch {/* ignore */}
    }, 3000);
  };
  useEffect(() => {
    if (running) startPolling();
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [running]);

  const startScan = async () => {
    setRunning(true);
    try {
      const res = await fetch(`${API_URL}/api/wheel/scan`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(cfg),
      });
      const s = await res.json();
      setStatus(s);
      setRunning(false);
      router.refresh();
    } catch (e) {
      console.error(e);
      setRunning(false);
    }
  };

  const finished = !running && (status.finished_at ?? status.started_at);

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-2">
        {running && (
          <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            Scanning chains…
          </span>
        )}
        <button
          onClick={() => setOpen(o => !o)}
          className="border-input hover:bg-muted inline-flex h-9 items-center rounded-md border px-3 text-xs"
        >
          Tune
        </button>
        <button
          disabled={running}
          onClick={startScan}
          className={`inline-flex h-9 items-center gap-2 rounded-md px-4 text-sm font-medium transition-colors ${
            running
              ? "border-input bg-muted pointer-events-none border opacity-60"
              : "bg-primary text-primary-foreground hover:bg-primary/90"
          }`}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          {running ? "Scan running…" : (hasCandidates ? "Re-scan" : "Run scan")}
        </button>
      </div>
      {!running && finished && (
        <span className="text-muted-foreground text-[11px]">
          {status.candidates ?? 0} candidates · {status.scanned ?? 0} symbols scanned
          {status.duration_seconds ? ` · ${status.duration_seconds.toFixed(1)}s` : ""}
        </span>
      )}

      {open && (
        <div className="absolute right-8 top-24 z-30 w-80 rounded-lg border bg-background p-4 text-xs shadow-lg space-y-2">
          <div className="font-semibold text-sm">Scan parameters</div>
          <NumberField label="Target DTE"          value={cfg.target_dte}             step={1}    onChange={v => setCfg(c => ({ ...c, target_dte: v }))} />
          <NumberField label="Min annualized yield" value={cfg.min_annualized_yield}  step={0.01} onChange={v => setCfg(c => ({ ...c, min_annualized_yield: v }))} suffix="(0.10 = 10%)" />
          <NumberField label="Max annualized yield" value={cfg.max_annualized_yield}  step={0.05} onChange={v => setCfg(c => ({ ...c, max_annualized_yield: v }))} suffix="(yields above this = trap)" />
          <NumberField label="CSP OTM target"       value={cfg.target_csp_otm_pct}    step={0.01} onChange={v => setCfg(c => ({ ...c, target_csp_otm_pct: v }))} suffix="(% below spot)" />
          <NumberField label="CC OTM target"        value={cfg.target_cc_otm_pct}     step={0.01} onChange={v => setCfg(c => ({ ...c, target_cc_otm_pct: v }))} suffix="(% above spot)" />
          <NumberField label="Min open interest"    value={cfg.min_open_interest}     step={10}   onChange={v => setCfg(c => ({ ...c, min_open_interest: v }))} />
          <NumberField label="Max IV"               value={cfg.max_implied_volatility} step={0.05} onChange={v => setCfg(c => ({ ...c, max_implied_volatility: v }))} suffix="(0.55 = drop high-vol names)" />
          <NumberField label="Max symbols to scan"  value={cfg.max_candidates_to_scan} step={10}  onChange={v => setCfg(c => ({ ...c, max_candidates_to_scan: v }))} suffix="(slower = more)" />
          <div className="text-muted-foreground pt-1 italic">
            yfinance is rate-limited; each scan is ~2-4s per symbol.
          </div>
        </div>
      )}
    </div>
  );
}

function NumberField({
  label, value, step, onChange, suffix,
}: { label: string; value: number; step: number; onChange: (v: number) => void; suffix?: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <label className="text-muted-foreground flex-1">
        {label} {suffix && <span className="opacity-70">{suffix}</span>}
      </label>
      <input
        type="number"
        step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="border-input bg-background h-7 w-20 rounded border px-2 text-right tabular-nums"
      />
    </div>
  );
}
