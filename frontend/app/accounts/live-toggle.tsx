"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

export function LiveToggle({
  accountId,
  currentLive,
  accountType,
  accountNumber,
}: {
  accountId: string;
  currentLive: boolean;
  accountType: string;
  accountNumber: string;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/accounts/${accountId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ real_money_enabled: !currentLive }),
      });
      if (!res.ok) throw new Error(await res.text());
      router.refresh();
      setConfirming(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  if (confirming && !currentLive) {
    return (
      <div className="space-y-2 rounded-md border border-destructive/50 bg-destructive/5 p-3 text-xs">
        <p className="font-semibold text-destructive">Enable REAL money execution?</p>
        <p className="text-muted-foreground">
          The monitor will place actual buy and stop-loss orders on your {accountType} account
          (#{accountNumber}) in Questrade when tickets trigger. This is real money.
        </p>
        <div className="flex gap-2 pt-1">
          <button
            onClick={toggle}
            disabled={loading}
            className="bg-destructive text-destructive-foreground rounded px-3 py-1 text-xs font-medium disabled:opacity-50"
          >
            {loading ? "Enabling…" : "Yes, enable live trading"}
          </button>
          <button
            onClick={() => setConfirming(false)}
            className="border-input hover:bg-muted rounded border px-3 py-1 text-xs"
          >
            Cancel
          </button>
        </div>
        {error && <p className="text-destructive">{error}</p>}
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-foreground">
        {currentLive ? "Live execution enabled" : "Paper mode (simulated)"}
      </span>
      <button
        onClick={() => currentLive ? toggle() : setConfirming(true)}
        disabled={loading}
        className={`text-xs rounded px-2 py-1 border font-medium transition-colors disabled:opacity-50 ${
          currentLive
            ? "border-muted text-muted-foreground hover:border-destructive hover:text-destructive"
            : "border-primary/30 text-primary hover:border-primary hover:bg-primary/5"
        }`}
      >
        {loading ? "…" : currentLive ? "Switch to paper" : "Enable live"}
      </button>
    </div>
  );
}
