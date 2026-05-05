"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_URL } from "@/lib/api";
import { fmtMoney } from "@/lib/tickets";

interface ExitLeg {
  price: string;
  shares: number;
  label: string;
  hit: boolean;
}

interface ExitPlan {
  targets: ExitLeg[];
}

export function ExitPlanForm({
  ticketId,
  currency,
  totalShares,
  triggerPrice,
  stopPrice,
  currentPlan,
}: {
  ticketId: string;
  currency: string;
  totalShares: number;
  triggerPrice: string;
  stopPrice: string;
  currentPlan: ExitPlan | null;
}) {
  const router = useRouter();
  const router2 = useRouter();
  const risk = parseFloat(triggerPrice) - parseFloat(stopPrice);

  // Default: 3-leg Minervini exit plan
  const [legs, setLegs] = useState<{ price: string; shares: string; label: string }[]>(
    currentPlan?.targets.map(t => ({
      price: t.price,
      shares: String(t.shares),
      label: t.label,
    })) ?? [
      { price: risk > 0 ? (parseFloat(triggerPrice) + risk * 1.5).toFixed(2) : "", shares: Math.floor(totalShares / 3).toString(), label: "T1 +1.5R" },
      { price: risk > 0 ? (parseFloat(triggerPrice) + risk * 2.5).toFixed(2) : "", shares: Math.floor(totalShares / 3).toString(), label: "T2 +2.5R" },
      { price: risk > 0 ? (parseFloat(triggerPrice) + risk * 4.0).toFixed(2) : "", shares: (totalShares - 2 * Math.floor(totalShares / 3)).toString(), label: "T3 +4R (trail)" },
    ]
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = (i: number, k: string, v: string) => {
    setLegs(prev => prev.map((l, j) => j === i ? { ...l, [k]: v } : l));
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/tickets/${ticketId}/exit-plan`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          targets: legs.map(l => ({
            price: parseFloat(l.price),
            shares: parseInt(l.shares),
            label: l.label,
          })),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Show current hits */}
      {currentPlan?.targets.some(t => t.hit) && (
        <div className="rounded-md bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 p-3 text-xs space-y-1">
          <p className="font-medium text-emerald-700 dark:text-emerald-300">Targets hit:</p>
          {currentPlan.targets.filter(t => t.hit).map((t, i) => (
            <div key={i} className="text-emerald-600 dark:text-emerald-400">
              ✓ {t.label} @ {fmtMoney(t.price, currency)}
            </div>
          ))}
        </div>
      )}

      {legs.map((leg, i) => {
        const alreadyHit = currentPlan?.targets[i]?.hit;
        return (
          <div key={i} className={`grid grid-cols-3 gap-2 items-end ${alreadyHit ? "opacity-50" : ""}`}>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">{leg.label || `Target ${i + 1}`} price</Label>
              <Input
                type="number"
                step="0.01"
                value={leg.price}
                onChange={e => update(i, "price", e.target.value)}
                className="tabular-nums text-sm"
                disabled={alreadyHit}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Shares</Label>
              <Input
                type="number"
                min="1"
                value={leg.shares}
                onChange={e => update(i, "shares", e.target.value)}
                className="tabular-nums text-sm"
                disabled={alreadyHit}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-xs">Label</Label>
              <Input
                value={leg.label}
                onChange={e => update(i, "label", e.target.value)}
                className="text-sm"
                disabled={alreadyHit}
              />
            </div>
          </div>
        );
      })}

      {error && <p className="text-destructive text-xs">{error}</p>}

      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={saving}
          className="bg-primary text-primary-foreground inline-flex h-8 items-center rounded-md px-4 text-xs font-medium disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save exit plan"}
        </button>
        <button
          onClick={() => setLegs(prev => [...prev, { price: "", shares: "1", label: `T${prev.length + 1}` }])}
          className="border-input hover:bg-muted inline-flex h-8 items-center rounded-md border px-3 text-xs"
        >
          + Add leg
        </button>
      </div>
    </div>
  );
}
