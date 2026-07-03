"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { API_URL } from "@/lib/api";

export function ActiveAccountButton({
  accountId,
  isActive,
  anyActive,
}: {
  accountId: string;
  isActive: boolean;
  anyActive: boolean;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setActive = async (target: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_URL}/api/accounts/active`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ account_id: target }),
      });
      if (!res.ok) throw new Error(await res.text());
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-foreground">
        {isActive
          ? "Trading account — sizing, risk & journal scope to this account"
          : anyActive
            ? "Hidden from sizing, risk & journal views"
            : "No scope set — all accounts count toward sizing & risk"}
      </span>
      <button
        onClick={() => setActive(isActive ? null : accountId)}
        disabled={loading}
        className={`text-xs rounded px-2 py-1 border font-medium transition-colors disabled:opacity-50 ${
          isActive
            ? "border-primary bg-primary/10 text-primary hover:bg-primary/5"
            : "border-input text-muted-foreground hover:border-primary hover:text-primary"
        }`}
      >
        {loading ? "…" : isActive ? "Clear scope" : "Set as trading account"}
      </button>
      {error && <p className="text-destructive text-xs">{error}</p>}
    </div>
  );
}
