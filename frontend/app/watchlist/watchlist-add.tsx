"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { API_URL } from "@/lib/api";

export function WatchlistAdd() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [pivot, setPivot] = useState("");
  const [needsPivot, setNeedsPivot] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/watchlist`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          symbol: symbol.trim().toUpperCase(),
          pivot_price: pivot ? parseFloat(pivot) : null,
        }),
      });
      if (res.status === 409) {
        setError("Already on your watchlist.");
        setLoading(false);
        return;
      }
      if (res.status === 422) {
        setNeedsPivot(true);
        setError("No pivot known for this symbol yet — enter one manually.");
        setLoading(false);
        return;
      }
      if (!res.ok) throw new Error(await res.text());
      setSymbol("");
      setPivot("");
      setNeedsPivot(false);
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="border-input hover:bg-muted inline-flex h-9 items-center rounded-md border px-4 text-sm font-medium"
      >
        + Add symbol
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="border-input flex flex-wrap items-end gap-2 rounded-md border p-3">
      <div>
        <label className="text-muted-foreground mb-1 block text-xs">Symbol</label>
        <Input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder="AAPL"
          className="h-9 w-28 font-mono uppercase"
          required
        />
      </div>
      {needsPivot && (
        <div>
          <label className="text-muted-foreground mb-1 block text-xs">Pivot price</label>
          <Input
            value={pivot}
            onChange={(e) => setPivot(e.target.value)}
            placeholder="52.00"
            type="number"
            step="0.01"
            className="h-9 w-28"
            required
          />
        </div>
      )}
      <button
        type="submit"
        disabled={loading}
        className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium disabled:opacity-50"
      >
        {loading ? "Adding…" : "Add"}
      </button>
      <button
        type="button"
        onClick={() => { setOpen(false); setError(null); }}
        className="border-input hover:bg-muted inline-flex h-9 items-center rounded-md border px-4 text-sm font-medium"
      >
        Cancel
      </button>
      {error && <p className="text-destructive w-full text-xs">{error}</p>}
    </form>
  );
}
