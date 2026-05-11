"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { FileText, Bookmark } from "lucide-react";
import { API_URL } from "@/lib/api";
import { type Position } from "@/lib/positions";
import { type Account } from "@/lib/tickets";
import { RetroactiveTicketDialog } from "./retroactive-ticket-dialog";

interface Props {
  position: Position;
  account?: Account;
  equityByCurrency?: Record<string, number>;
}

export function UnmanagedActions({ position, account, equityByCurrency }: Props) {
  const router = useRouter();
  const [showDialog, setShowDialog]   = useState(false);
  const [busy, setBusy]               = useState(false);
  const [err, setErr]                 = useState<string | null>(null);

  const markBuyAndHold = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await fetch(`${API_URL}/api/positions/${position.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_buy_and_hold: true }),
      });
      if (!res.ok) throw new Error(await res.text());
      router.refresh();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mt-2 flex items-center gap-1.5">
        <button
          onClick={() => setShowDialog(true)}
          className="inline-flex h-7 flex-1 items-center justify-center gap-1 rounded border border-primary/40 bg-primary/10 px-2 text-[11px] font-medium text-primary hover:bg-primary/20 transition-colors"
        >
          <FileText className="h-3 w-3" />
          Make ticket
        </button>
        <button
          onClick={markBuyAndHold} disabled={busy}
          className="inline-flex h-7 items-center gap-1 rounded border border-border/60 px-2 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-50"
          title="Long-term hold — silence the unmanaged warning"
        >
          <Bookmark className="h-3 w-3" />
          Buy & hold
        </button>
      </div>
      {err && <p className="text-destructive mt-1 text-[10px]">{err}</p>}

      {showDialog && (
        <RetroactiveTicketDialog
          position={position}
          account={account}
          equityByCurrency={equityByCurrency}
          onClose={() => setShowDialog(false)}
        />
      )}
    </>
  );
}
