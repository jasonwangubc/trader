"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, X } from "lucide-react";
import { API_URL } from "@/lib/api";

/** Small popover button for adding a custom symbol to the screener universe.
 *
 * The screener universe is auto-built nightly from S&P 500/400/600, NASDAQ 100,
 * TSX 60, and all SEC-listed US stocks. This widget lets the user manually
 * add anything outside that set (foreign listings, OTC names, etc.). After
 * adding, the user must run a scan to score it.
 */
export function AddSymbolButton() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [justAdded, setJustAdded] = useState<string | null>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  // Auto-focus input when opened
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const submit = async () => {
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/screener/watchlist`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ symbol: sym }),
      });
      if (!res.ok) throw new Error(await res.text());
      setJustAdded(sym);
      setSymbol("");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative" ref={popRef}>
      <button
        onClick={() => setOpen(o => !o)}
        className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border/60 bg-card px-3 text-sm font-medium hover:bg-muted/50 transition-colors"
        title="Add a custom symbol to the screener universe"
      >
        <Plus className="h-3.5 w-3.5" />
        Symbol
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-20 w-72 rounded-lg border border-border/60 bg-card p-3 shadow-lg">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Add custom symbol
            </h3>
            <button
              onClick={() => setOpen(false)}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <p className="text-[11px] text-muted-foreground mb-2 leading-relaxed">
            The universe auto-builds from S&P 500/400/600, NASDAQ 100, TSX 60, and all
            SEC-listed US stocks. Add anything outside that set here.
          </p>
          <div className="flex gap-1.5">
            <input
              ref={inputRef}
              value={symbol}
              onChange={e => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && submit()}
              placeholder="AAPL or BAM.TO"
              className="font-mono uppercase flex-1 h-8 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
              disabled={busy}
            />
            <button
              onClick={submit}
              disabled={busy || !symbol.trim()}
              className="bg-primary text-primary-foreground inline-flex h-8 items-center rounded px-3 text-xs font-medium disabled:opacity-50 hover:bg-primary/90 transition-colors"
            >
              {busy ? "…" : "Add"}
            </button>
          </div>
          {error && <p className="text-destructive text-xs mt-2">{error}</p>}
          {justAdded && (
            <p className="text-emerald-500 text-xs mt-2">
              Added <span className="font-mono">{justAdded}</span> — run a scan to score it.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
