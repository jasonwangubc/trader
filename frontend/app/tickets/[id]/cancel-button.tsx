"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

export function CancelButton({ ticketId, symbol }: { ticketId: string; symbol: string }) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cancel = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/tickets/${ticketId}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      router.push("/tickets");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  };

  if (!confirming) {
    return (
      <button
        onClick={() => setConfirming(true)}
        className="border-destructive/50 text-destructive hover:bg-destructive/10 inline-flex h-9 items-center rounded-md border px-4 text-sm font-medium"
      >
        Cancel {symbol} ticket
      </button>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm">Are you sure you want to cancel this ticket? It will no longer be watched for triggers.</p>
      <div className="flex gap-2">
        <button
          onClick={cancel}
          disabled={loading}
          className="bg-destructive text-destructive-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium disabled:opacity-50"
        >
          {loading ? "Cancelling…" : "Yes, cancel"}
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="border-input hover:bg-muted inline-flex h-9 items-center rounded-md border px-4 text-sm font-medium"
        >
          Keep it
        </button>
      </div>
      {error && <p className="text-destructive text-xs">{error}</p>}
    </div>
  );
}
