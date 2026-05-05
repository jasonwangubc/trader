"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { API_URL } from "@/lib/api";

interface Account {
  id: string;
  questrade_account_id: string;
  type: string;
  primary_currency: string;
}

export function NewOptionForm({ accounts }: { accounts: Account[] }) {
  const router = useRouter();
  const [strategy, setStrategy] = useState("covered_call");
  const [form, setForm] = useState({
    account_id: accounts[0]?.id ?? "",
    underlying_symbol: "",
    currency: "USD",
    strike_price: "",
    expiry_date: "",
    contracts: "1",
    premium_received: "",
    thesis: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof typeof form, v: string) => setForm(f => ({ ...f, [k]: v }));

  const totalPremium = (() => {
    const p = parseFloat(form.premium_received);
    const c = parseInt(form.contracts);
    if (!isFinite(p) || !isFinite(c)) return null;
    return (p * 100 * c).toFixed(2);
  })();

  const breakEven = (() => {
    const s = parseFloat(form.strike_price);
    const p = parseFloat(form.premium_received);
    if (!isFinite(s) || !isFinite(p)) return null;
    return (s - p).toFixed(2);
  })();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/api/options`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...form,
          strategy,
          strike_price: parseFloat(form.strike_price),
          contracts: parseInt(form.contracts),
          premium_received: parseFloat(form.premium_received),
        }),
      });
      if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
      router.refresh();
      setForm(f => ({ ...f, underlying_symbol: "", strike_price: "", expiry_date: "", premium_received: "", thesis: "" }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Log new position</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label>Strategy</Label>
            <select
              value={strategy}
              onChange={e => setStrategy(e.target.value)}
              className="border-input bg-background h-9 rounded-md border px-3 text-sm"
            >
              <option value="covered_call">Covered Call</option>
              <option value="cash_secured_put">Cash-Secured Put</option>
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Account</Label>
            <select
              value={form.account_id}
              onChange={e => set("account_id", e.target.value)}
              className="border-input bg-background h-9 rounded-md border px-3 text-sm"
            >
              {accounts.map(a => (
                <option key={a.id} value={a.id}>
                  {a.type} · #{a.questrade_account_id}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Underlying</Label>
              <Input
                value={form.underlying_symbol}
                onChange={e => set("underlying_symbol", e.target.value.toUpperCase())}
                placeholder="AAPL"
                className="font-mono uppercase"
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Currency</Label>
              <select
                value={form.currency}
                onChange={e => set("currency", e.target.value)}
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
              >
                <option value="USD">USD</option>
                <option value="CAD">CAD</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Strike price</Label>
              <Input type="number" step="0.50" min="0.01" value={form.strike_price}
                onChange={e => set("strike_price", e.target.value)} className="tabular-nums" required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Expiry date</Label>
              <Input type="date" value={form.expiry_date}
                onChange={e => set("expiry_date", e.target.value)} required />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label>Premium / share</Label>
              <Input type="number" step="0.01" min="0.01" value={form.premium_received}
                onChange={e => set("premium_received", e.target.value)} className="tabular-nums" required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Contracts</Label>
              <Input type="number" min="1" value={form.contracts}
                onChange={e => set("contracts", e.target.value)} className="tabular-nums" required />
            </div>
          </div>

          {(totalPremium || breakEven) && (
            <div className="rounded-md bg-muted/50 p-2 text-xs space-y-1">
              {totalPremium && <div className="flex justify-between"><span className="text-muted-foreground">Total premium</span><span className="font-semibold">${totalPremium}</span></div>}
              {breakEven && <div className="flex justify-between"><span className="text-muted-foreground">Break-even</span><span>${breakEven}</span></div>}
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <Label>Thesis (optional)</Label>
            <Textarea
              value={form.thesis}
              onChange={e => set("thesis", e.target.value)}
              placeholder="Why this strike and expiry?"
              rows={2}
            />
          </div>

          {error && <p className="text-destructive text-xs">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="bg-primary text-primary-foreground w-full rounded-md py-2 text-sm font-medium disabled:opacity-50"
          >
            {submitting ? "Logging…" : "Log position"}
          </button>
        </form>
      </CardContent>
    </Card>
  );
}
