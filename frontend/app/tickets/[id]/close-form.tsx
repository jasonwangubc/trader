"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_URL } from "@/lib/api";

export function CloseForm({ ticketId, currency }: { ticketId: string; currency: string }) {
  const router = useRouter();
  const [exitPrice, setExitPrice] = useState("");
  const [exitReason, setExitReason] = useState("manual");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/tickets/${ticketId}/close`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ exit_price: parseFloat(exitPrice), exit_reason: exitReason }),
      });
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-wrap items-end gap-4">
      <div className="flex flex-col gap-1.5">
        <Label>Exit price ({currency})</Label>
        <Input
          type="number"
          step="0.01"
          min="0.01"
          value={exitPrice}
          onChange={(e) => setExitPrice(e.target.value)}
          className="w-36 tabular-nums"
          required
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label>Reason</Label>
        <select
          value={exitReason}
          onChange={(e) => setExitReason(e.target.value)}
          className="border-input bg-background h-9 rounded-md border px-3 text-sm"
        >
          <option value="manual">Manual exit</option>
          <option value="stop_hit">Stop hit</option>
          <option value="target_hit">Target hit</option>
          <option value="time_stop">Time stop</option>
        </select>
      </div>
      <button
        type="submit"
        disabled={!exitPrice || submitting}
        className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium transition-colors hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting ? "Saving…" : "Record exit"}
      </button>
      {error && <p className="text-destructive w-full text-sm">{error}</p>}
    </form>
  );
}
