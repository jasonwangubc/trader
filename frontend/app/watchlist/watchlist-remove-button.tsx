"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";
import { API_URL } from "@/lib/api";

export function WatchlistRemoveButton({ itemId, symbol }: { itemId: string; symbol: string }) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);

  const remove = async () => {
    if (!confirm(`Remove ${symbol} from the watchlist?`)) return;
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/api/watchlist/${itemId}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      router.refresh();
    } catch {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={remove}
      disabled={loading}
      title={`Remove ${symbol}`}
      className="border-input hover:bg-muted inline-flex h-8 items-center gap-1 rounded-md border px-2 text-xs disabled:opacity-50"
    >
      <X className="h-3.5 w-3.5" />
    </button>
  );
}
