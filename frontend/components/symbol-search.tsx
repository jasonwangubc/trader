"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { Search, TrendingUp, BarChart2, X } from "lucide-react";
import { API_URL } from "@/lib/api";

interface SearchHit {
  symbol: string;
  sector: string | null;
  tt_score: number | null;
  composite_score: number | null;
}

export function SymbolSearch() {
  const router = useRouter();
  const [open, setOpen]       = useState(false);
  const [query, setQuery]     = useState("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // CMD+K / CTRL+K to open
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen(o => !o);
        setQuery("");
        setSelected(0);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  // Debounced server-side search across the full universe (not just top-scored).
  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        const url = `${API_URL}/api/screener/search?q=${encodeURIComponent(query)}&limit=12`;
        const r = await fetch(url, { signal: ctrl.signal });
        if (r.ok) {
          const data: SearchHit[] = await r.json();
          setResults(data);
          setSelected(0);
        }
      } catch {/* aborted */}
    }, query ? 120 : 0);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [query, open]);

  const go = useCallback((symbol: string, dest: "chart" | "ticket") => {
    setOpen(false);
    setQuery("");
    if (dest === "chart") router.push(`/chart/${symbol}`);
    else router.push(`/tickets/new?symbol=${symbol}`);
  }, [router]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSelected(s => Math.min(s + 1, results.length - 1)); }
    if (e.key === "ArrowUp")   { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)); }
    if (e.key === "Enter" && results[selected]) go(results[selected].symbol, "chart");
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="hidden lg:flex items-center gap-2 rounded-md border border-input bg-muted/30 px-3 h-8 text-sm text-muted-foreground hover:bg-muted transition-colors w-full mx-2 my-1"
      >
        <Search className="h-3.5 w-3.5" />
        <span>Search symbol…</span>
        <kbd className="ml-auto text-[10px] bg-muted rounded px-1">⌘K</kbd>
      </button>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh] px-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="relative w-full max-w-md rounded-xl border bg-background shadow-2xl overflow-hidden">
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b">
          <Search className="h-4 w-4 text-muted-foreground shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search symbol or sector…"
            className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
          {query && (
            <button onClick={() => setQuery("")} className="text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto py-1">
          {results.length === 0 ? (
            <div className="px-4 py-6 text-center text-sm text-muted-foreground">No symbols found</div>
          ) : results.map((r, i) => (
            <div
              key={r.symbol}
              className={`flex items-center justify-between px-4 py-2.5 cursor-pointer transition-colors ${
                i === selected ? "bg-accent" : "hover:bg-accent/50"
              }`}
              onMouseEnter={() => setSelected(i)}
              onClick={() => go(r.symbol, "chart")}
            >
              <div className="flex items-center gap-3">
                <span className="font-mono font-semibold text-sm w-16">{r.symbol}</span>
                {r.sector && <span className="text-muted-foreground text-xs">{r.sector}</span>}
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-muted-foreground">
                  {r.tt_score !== null ? `TT ${r.tt_score}/8` : "unscored"}
                </span>
                <span className="text-xs font-medium">
                  {r.composite_score !== null ? Math.round(r.composite_score) : "—"}
                </span>
                <div className="flex gap-1">
                  <button
                    onClick={e => { e.stopPropagation(); go(r.symbol, "chart"); }}
                    className="text-muted-foreground hover:text-foreground p-1 rounded"
                    title="View chart"
                  >
                    <BarChart2 className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={e => { e.stopPropagation(); go(r.symbol, "ticket"); }}
                    className="text-muted-foreground hover:text-foreground p-1 rounded"
                    title="Arm ticket"
                  >
                    <TrendingUp className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>

        <div className="border-t px-4 py-2 text-[10px] text-muted-foreground flex gap-4">
          <span>↑↓ navigate</span>
          <span>↵ chart</span>
          <span>Esc close</span>
        </div>
      </div>
    </div>
  );
}
