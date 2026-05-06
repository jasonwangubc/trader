"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { API_URL } from "@/lib/api";
import { fmtMoney } from "@/lib/tickets";

export function PyramidForm({
  ticketId,
  currency,
  currentShares,
  stopPrice,
}: {
  ticketId: string;
  currency: string;
  currentShares: number;
  stopPrice: string;
}) {
  const router = useRouter();
  const [addPrice, setAddPrice]   = useState("");
  const [addShares, setAddShares] = useState(Math.floor(currentShares / 3).toString());
  const [saving, setSaving]       = useState(false);
  const [error, setError]         = useState<string | null>(null);

  const newTotalShares = currentShares + (parseInt(addShares) || 0);
  const riskOnAdd = addPrice && addShares
    ? ((parseFloat(addPrice) - parseFloat(stopPrice)) * parseInt(addShares)).toFixed(2)
    : null;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/tickets/${ticketId}/pyramid`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ add_price: parseFloat(addPrice), add_shares: parseInt(addShares) }),
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
      <p className="text-muted-foreground text-xs">
        Adding shares to an existing position. The stop at ${parseFloat(stopPrice).toFixed(2)} is <strong>immutable</strong> and manages the full combined position.
        Typically add 1/3 of original size when stock is +2–3% above entry and acting well.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Add-on price</Label>
          <Input
            type="number"
            step="0.01"
            value={addPrice}
            onChange={e => setAddPrice(e.target.value)}
            className="tabular-nums text-sm"
            placeholder="150.00"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="text-xs">Shares to add</Label>
          <Input
            type="number"
            min="1"
            value={addShares}
            onChange={e => setAddShares(e.target.value)}
            className="tabular-nums text-sm"
          />
        </div>
      </div>

      {riskOnAdd && (
        <div className="rounded bg-muted/50 p-2 text-xs space-y-1">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Additional risk at stop</span>
            <span className="font-semibold text-amber-600 dark:text-amber-400">-{fmtMoney(riskOnAdd, currency)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">New total position</span>
            <span>{newTotalShares.toLocaleString()} shares</span>
          </div>
        </div>
      )}

      {error && <p className="text-destructive text-xs">{error}</p>}

      <button
        onClick={save}
        disabled={saving || !addPrice || !addShares}
        className="bg-primary text-primary-foreground inline-flex h-8 items-center rounded-md px-4 text-xs font-medium disabled:opacity-50"
      >
        {saving ? "Adding…" : "Record add-on entry"}
      </button>
    </div>
  );
}
