"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { API_URL } from "@/lib/api";
import { type ScreenerSymbol } from "@/lib/screener";

export function WatchlistManager({ initialSymbols }: { initialSymbols: ScreenerSymbol[] }) {
  const router = useRouter();
  const [symbols, setSymbols] = useState(initialSymbols);
  const [newSymbol, setNewSymbol] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    const sym = newSymbol.trim().toUpperCase();
    if (!sym) return;
    setAdding(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/screener/watchlist`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ symbol: sym }),
      });
      if (!res.ok) throw new Error(await res.text());
      const added = await res.json();
      setSymbols((prev) => [...prev.filter((s) => s.symbol !== sym), added]);
      setNewSymbol("");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const remove = async (symbol: string) => {
    await fetch(`${API_URL}/api/screener/watchlist/${symbol}`, { method: "DELETE" });
    setSymbols((prev) => prev.filter((s) => s.symbol !== symbol));
    router.refresh();
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Watchlist</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && add()}
            placeholder="AAPL"
            className="font-mono uppercase"
          />
          <button
            onClick={add}
            disabled={adding || !newSymbol.trim()}
            className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-3 text-sm font-medium disabled:opacity-50"
          >
            Add
          </button>
        </div>
        {error && <p className="text-destructive text-xs">{error}</p>}
        <div className="space-y-1">
          {symbols.length === 0 && (
            <p className="text-muted-foreground text-sm">No symbols yet.</p>
          )}
          {symbols.map((s) => (
            <div key={s.symbol} className="flex items-center justify-between rounded-md px-2 py-1 hover:bg-muted/50">
              <span className="font-mono text-sm">{s.symbol}</span>
              <button
                onClick={() => remove(s.symbol)}
                className="text-muted-foreground hover:text-destructive text-xs"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
