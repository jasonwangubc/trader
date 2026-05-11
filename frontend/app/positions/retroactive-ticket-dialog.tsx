"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";
import { API_URL } from "@/lib/api";
import { type Position } from "@/lib/positions";
import { fmtMoney, type Account } from "@/lib/tickets";

const SETUP_OPTIONS = [
  { value: "manual",     label: "Manual / unclassified" },
  { value: "VCP",        label: "VCP" },
  { value: "flat_base",  label: "Flat base" },
  { value: "ep",         label: "Earnings pivot (EP)" },
  { value: "cup_handle", label: "Cup with handle" },
  { value: "pivot",      label: "Pivot breakout" },
];

interface Props {
  position: Position;
  account?: Account;
  equityByCurrency?: Record<string, number>; // optional — used to compute risk %
  onClose: () => void;
}

export function RetroactiveTicketDialog({ position, account, equityByCurrency, onClose }: Props) {
  const router = useRouter();
  const [stop, setStop]       = useState(position.broker_stop_price ?? "");
  const [target, setTarget]   = useState(position.broker_target_price ?? "");
  const [setup, setSetup]     = useState("manual");
  const [thesis, setThesis]   = useState("");
  const [busy, setBusy]       = useState(false);
  const [error, setError]     = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Close on Escape, click outside
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const onClick = (e: MouseEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [onClose]);

  const entryPrice = parseFloat(position.avg_cost);
  const stopNum    = parseFloat(stop);
  const qty        = parseFloat(position.quantity);
  const validStop  = stopNum > 0 && stopNum < entryPrice;

  const perShareRisk = validStop ? entryPrice - stopNum : 0;
  const riskAmount   = validStop ? perShareRisk * qty : 0;
  const stopPctDown  = validStop ? ((entryPrice - stopNum) / entryPrice) * 100 : 0;
  const targetNum    = parseFloat(target);
  const rr           = (validStop && targetNum > entryPrice)
    ? (targetNum - entryPrice) / perShareRisk
    : null;
  const equity       = equityByCurrency?.[position.currency] ?? 0;
  const riskPct      = (validStop && equity > 0) ? (riskAmount / equity) * 100 : null;

  const submit = async () => {
    setError(null);
    if (!validStop) {
      setError("Stop must be below entry price.");
      return;
    }
    if (thesis.trim().length < 10) {
      setError("Thesis must be at least 10 characters.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${API_URL}/api/tickets/retroactive`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          position_id: position.id,
          stop_price: stopNum,
          target_price: targetNum > 0 ? targetNum : null,
          setup_type: setup,
          thesis: thesis.trim(),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `${res.status}`);
      }
      router.refresh();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-4">
      <div ref={dialogRef} className="w-full max-w-lg rounded-xl border bg-card shadow-xl">
        <div className="flex items-center justify-between px-5 py-3 border-b">
          <div>
            <h2 className="text-base font-semibold">Adopt position into a ticket</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              Records {position.symbol} as a filled ticket so it shows up in risk + journal.
              No regime, sizing, or streak gates — the position already exists.
            </p>
          </div>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-5 py-4 space-y-3">
          {/* Position summary */}
          <div className="rounded-md bg-muted/40 p-3 grid grid-cols-3 gap-2 text-xs">
            <Stat label="Symbol"    value={position.symbol} mono />
            <Stat label="Quantity"  value={qty.toLocaleString()} />
            <Stat label="Account"   value={account?.type ?? "—"} />
            <Stat label="Avg cost"  value={fmtMoney(position.avg_cost, position.currency)} />
            <Stat label="Last"      value={position.current_price ? fmtMoney(position.current_price, position.currency) : "—"} />
            <Stat label="Market"    value={fmtMoney(position.market_value, position.currency)} />
          </div>

          {/* Stop + target */}
          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Stop price *"
              hint={position.broker_stop_price
                ? `From Questrade: ${fmtMoney(position.broker_stop_price, position.currency)}`
                : "Where you'll exit if it goes against you"}
            >
              <input
                type="number" step="0.01" min="0"
                value={stop} onChange={e => setStop(e.target.value)}
                className="font-mono w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="0.00"
              />
            </Field>
            <Field
              label="Target price"
              hint={position.broker_target_price
                ? `From Questrade: ${fmtMoney(position.broker_target_price, position.currency)}`
                : "Optional — where you'd take profits"}
            >
              <input
                type="number" step="0.01" min="0"
                value={target} onChange={e => setTarget(e.target.value)}
                className="font-mono w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                placeholder="optional"
              />
            </Field>
          </div>

          {/* Risk readout */}
          {validStop && (
            <div className="rounded-md border border-border/40 bg-muted/20 p-3 text-xs grid grid-cols-4 gap-2">
              <Stat label="Risk $"   value={fmtMoney(String(riskAmount.toFixed(2)), position.currency)} />
              <Stat label="Stop"     value={`-${stopPctDown.toFixed(1)}%`} />
              <Stat label="R:R"      value={rr ? `${rr.toFixed(1)}` : "—"} />
              <Stat label="% equity" value={riskPct !== null ? `${riskPct.toFixed(2)}%` : "—"} />
            </div>
          )}

          {/* Setup type */}
          <Field label="Setup type" hint="No discipline applied — this is for journaling.">
            <select
              value={setup} onChange={e => setSetup(e.target.value)}
              className="w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
            >
              {SETUP_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </Field>

          {/* Thesis */}
          <Field
            label="Thesis / notes *"
            hint="Why you bought it & what you're watching for. Min 10 chars. The honest 30-second version is fine."
          >
            <textarea
              value={thesis} onChange={e => setThesis(e.target.value)}
              rows={3}
              className="w-full rounded border border-border/60 bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
              placeholder="Bought into earnings beat, base broke out 4 weeks ago, want to ride the 50-day…"
            />
          </Field>

          {error && <p className="text-destructive text-xs">{error}</p>}
        </div>

        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t bg-muted/20">
          <button
            onClick={onClose} disabled={busy}
            className="h-9 rounded-md px-4 text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={submit} disabled={busy || !validStop || thesis.trim().length < 10}
            className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {busy ? "Creating…" : "Create ticket"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">{label}</div>
      <div className={`text-sm font-medium tabular-nums ${mono ? "font-mono" : ""}`}>{value}</div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs font-medium text-foreground">{label}</label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground mt-0.5">{hint}</p>}
    </div>
  );
}
