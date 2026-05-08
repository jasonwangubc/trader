"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function QuestradConnect({ initialConnected }: { initialConnected: boolean }) {
  const [token, setToken]         = useState("");
  const [saving, setSaving]       = useState(false);
  const [connected, setConnected] = useState(initialConnected);

  const save = async () => {
    if (!token.trim()) return;
    setSaving(true);
    try {
      const res = await fetch("/api/backend/api/settings/questrade/token", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh_token: token.trim() }),
      });
      if (!res.ok) throw new Error(await res.text());
      setConnected(true);
      setToken("");
      toast.success("Questrade connected successfully");
    } catch (e) {
      toast.error(`Failed to save token: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const disconnect = async () => {
    const res = await fetch("/api/backend/api/settings/questrade/token", { method: "DELETE" });
    if (res.ok) {
      setConnected(false);
      toast.success("Questrade disconnected");
    }
  };

  if (connected) {
    return (
      <div className="flex items-center justify-between">
        <div className="text-sm">
          <div className="font-medium text-emerald-600 dark:text-emerald-400">✓ Questrade is connected</div>
          <div className="text-muted-foreground text-xs mt-0.5">
            Sync your accounts from the <a href="/accounts" className="text-primary hover:underline">Accounts page</a> to load balances and positions.
          </div>
        </div>
        <button
          onClick={disconnect}
          className="border-input hover:text-destructive text-muted-foreground border rounded-md px-3 py-1.5 text-xs hover:border-destructive/50 transition-colors"
        >
          Disconnect
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="qt-token" className="text-xs">Questrade refresh token</Label>
        <div className="flex gap-2">
          <Input
            id="qt-token"
            type="password"
            value={token}
            onChange={e => setToken(e.target.value)}
            onKeyDown={e => e.key === "Enter" && save()}
            placeholder="Paste your token here"
            className="font-mono text-sm"
          />
          <button
            onClick={save}
            disabled={saving || !token.trim()}
            className="bg-primary text-primary-foreground px-4 rounded-md text-sm font-medium disabled:opacity-50 shrink-0"
          >
            {saving ? "Saving…" : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}
