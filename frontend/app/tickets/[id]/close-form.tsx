"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { API_URL } from "@/lib/api";

const REASON_TAGS = [
  { value: "",                   label: "— Select a reason —" },
  { value: "plan_target_hit",    label: "Target hit (followed plan)" },
  { value: "plan_stop_hit",      label: "Stop hit (followed plan)" },
  { value: "took_profit_early",  label: "Took profit early (cut winner)" },
  { value: "moved_stop_manually",label: "Manually moved stop" },
  { value: "panic_exit",         label: "Panic exit (emotional)" },
  { value: "earnings_fear",      label: "Exited before earnings" },
  { value: "market_deterioration",label: "Market weakened" },
  { value: "thesis_broken",      label: "Setup thesis broken" },
  { value: "held_too_long",      label: "Held too long (should have exited earlier)" },
  { value: "personal_liquidity", label: "Personal liquidity need" },
];

export function CloseForm({ ticketId, currency }: { ticketId: string; currency: string }) {
  const router = useRouter();
  const [exitPrice, setExitPrice]         = useState("");
  const [exitReason, setExitReason]       = useState("manual");
  const [closeReasonTag, setCloseReasonTag] = useState("");
  const [closeNotes, setCloseNotes]       = useState("");
  const [submitting, setSubmitting]       = useState(false);
  const [error, setError]                 = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/tickets/${ticketId}/close`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          exit_price: parseFloat(exitPrice),
          exit_reason: exitReason,
          close_reason_tag: closeReasonTag || null,
          close_notes: closeNotes.trim() || null,
        }),
      });
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label>Exit price ({currency})</Label>
          <Input
            type="number"
            step="0.01"
            min="0.01"
            value={exitPrice}
            onChange={e => setExitPrice(e.target.value)}
            className="w-36 tabular-nums"
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Exit type</Label>
          <select
            value={exitReason}
            onChange={e => setExitReason(e.target.value)}
            className="border-input bg-background h-9 rounded-md border px-3 text-sm"
          >
            <option value="manual">Manual exit</option>
            <option value="stop_hit">Stop hit</option>
            <option value="target_hit">Target hit</option>
            <option value="time_stop">Time stop</option>
          </select>
        </div>
      </div>

      {/* Journal fields */}
      <div className="space-y-3 rounded-lg border bg-muted/20 p-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          Trade journal (helps the behavioral coach)
        </p>
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Why did you exit?</Label>
          <select
            value={closeReasonTag}
            onChange={e => setCloseReasonTag(e.target.value)}
            className="border-input bg-background h-9 rounded-md border px-3 text-sm"
          >
            {REASON_TAGS.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Reflection (optional)</Label>
          <Textarea
            value={closeNotes}
            onChange={e => setCloseNotes(e.target.value)}
            placeholder="What happened? What would you do differently? Did you follow the plan?"
            rows={2}
            className="text-sm"
          />
        </div>
      </div>

      {error && <p className="text-destructive text-sm">{error}</p>}
      <button
        type="submit"
        disabled={!exitPrice || submitting}
        className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium transition-colors hover:bg-primary/90 disabled:opacity-50"
      >
        {submitting ? "Saving…" : "Record exit"}
      </button>
    </form>
  );
}
