"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Pencil, X } from "lucide-react";
import { API_URL } from "@/lib/api";
import { type Account, type Ticket, fmtMoney, SETUP_TYPES } from "@/lib/tickets";

interface Props {
  ticket: Ticket;
  accounts: Account[];
}

export function TicketEditForm({ ticket, accounts }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [accountId, setAccountId]   = useState(ticket.account_id);
  const [trigger, setTrigger]       = useState(ticket.trigger_price);
  const [stop, setStop]             = useState(ticket.stop_price);
  const [target, setTarget]         = useState(ticket.target_price ?? "");
  const [validDays, setValidDays]   = useState("7");
  const [setupType, setSetupType]   = useState(ticket.setup_type);
  const [thesis, setThesis]         = useState(ticket.thesis ?? "");

  const triggerN = parseFloat(trigger);
  const stopN    = parseFloat(stop);
  const riskPerShare = (triggerN > 0 && stopN > 0 && triggerN > stopN)
    ? (triggerN - stopN).toFixed(2) : null;

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      if (accountId !== ticket.account_id)       body.account_id              = accountId;
      if (trigger   !== ticket.trigger_price)    body.trigger_price           = parseFloat(trigger);
      if (stop      !== ticket.stop_price)       body.stop_price              = parseFloat(stop);
      if (target    !== (ticket.target_price ?? "")) {
        body.target_price = target ? parseFloat(target) : null;
      }
      if (setupType !== ticket.setup_type)       body.setup_type              = setupType;
      if (thesis    !== (ticket.thesis ?? ""))   body.thesis                  = thesis;
      if (validDays)                             body.valid_for_days          = parseInt(validDays, 10);

      if (Object.keys(body).length === 0) { setOpen(false); return; }

      const res = await fetch(`${API_URL}/api/tickets/${ticket.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `${res.status}`);
      }
      router.refresh();
      setOpen(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-md border border-border/60 px-3 h-8 text-xs font-medium text-muted-foreground hover:text-foreground hover:border-primary/50 transition-colors"
      >
        <Pencil className="h-3 w-3" /> Edit ticket
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm p-4">
          <div className="w-full max-w-lg rounded-xl border bg-card shadow-xl">
            <div className="flex items-center justify-between px-5 py-3 border-b">
              <div>
                <h2 className="text-base font-semibold">Edit ticket — {ticket.symbol}</h2>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Only armed tickets can be edited. Sizing recalculates automatically if you change account or prices.
                </p>
              </div>
              <button onClick={() => setOpen(false)} className="text-muted-foreground hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="px-5 py-4 space-y-4">
              {/* Account */}
              <Field label="Account">
                <select
                  value={accountId}
                  onChange={e => setAccountId(e.target.value)}
                  className="w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                >
                  {accounts.map(a => (
                    <option key={a.id} value={a.id}>
                      {a.type} #{a.questrade_account_id} ({a.primary_currency})
                      {!a.real_money_enabled ? " — paper" : ""}
                    </option>
                  ))}
                </select>
              </Field>

              {/* Setup type */}
              <Field label="Setup type">
                <select
                  value={setupType}
                  onChange={e => setSetupType(e.target.value)}
                  className="w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                >
                  {SETUP_TYPES.map(s => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </Field>

              {/* Prices */}
              <div className="grid grid-cols-3 gap-3">
                <Field label="Trigger price">
                  <input
                    type="number" step="0.01" min="0.01"
                    value={trigger}
                    onChange={e => setTrigger(e.target.value)}
                    className="font-mono w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                  />
                </Field>
                <Field label="Stop price">
                  <input
                    type="number" step="0.01" min="0.01"
                    value={stop}
                    onChange={e => setStop(e.target.value)}
                    className="font-mono w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                  />
                </Field>
                <Field label="Target (optional)">
                  <input
                    type="number" step="0.01" min="0.01"
                    value={target}
                    onChange={e => setTarget(e.target.value)}
                    placeholder="—"
                    className="font-mono w-full h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                  />
                </Field>
              </div>

              {riskPerShare && (
                <p className="text-xs text-muted-foreground">
                  Risk per share: <span className="font-mono text-foreground">${riskPerShare}</span>
                  {" · "}Sizing will recalculate on save.
                </p>
              )}

              {/* Valid for */}
              <Field label="Extend validity (days from now)">
                <input
                  type="number" min="1" max="90"
                  value={validDays}
                  onChange={e => setValidDays(e.target.value)}
                  className="w-28 h-9 rounded border border-border/60 bg-background px-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                />
              </Field>

              {/* Thesis */}
              <Field label="Thesis">
                <textarea
                  value={thesis}
                  onChange={e => setThesis(e.target.value)}
                  rows={3}
                  className="w-full rounded border border-border/60 bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                />
              </Field>

              {error && <p className="text-destructive text-xs">{error}</p>}
            </div>

            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t bg-muted/20">
              <button
                onClick={() => setOpen(false)} disabled={busy}
                className="h-9 rounded-md px-4 text-sm font-medium hover:bg-muted transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={submit} disabled={busy}
                className="bg-primary text-primary-foreground inline-flex h-9 items-center rounded-md px-4 text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {busy ? "Saving…" : "Save changes"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium text-foreground">{label}</label>
      {children}
    </div>
  );
}
