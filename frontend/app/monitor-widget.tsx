"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL } from "@/lib/api";

interface MonitorStatus {
  running: boolean;
  armed_tickets: number;
  last_tick_at: string | null;
  kill_switch: boolean;
  market_open: boolean;
}

export function MonitorWidget() {
  const [status, setStatus] = useState<MonitorStatus | null>(null);
  const [toggling, setToggling] = useState(false);

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_URL}/api/monitor/status`, { cache: "no-store" });
      if (res.ok) setStatus(await res.json());
    } catch {
      // silently ignore — backend may not be up
    }
  };

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 15_000);
    return () => clearInterval(id);
  }, []);

  const toggleKillSwitch = async () => {
    if (!status || toggling) return;
    setToggling(true);
    const endpoint = status.kill_switch ? "disable" : "enable";
    try {
      await fetch(`${API_URL}/api/monitor/kill-switch/${endpoint}`, { method: "POST" });
      await fetchStatus();
    } finally {
      setToggling(false);
    }
  };

  if (!status) return null;

  const lastTick = status.last_tick_at
    ? new Date(status.last_tick_at).toLocaleTimeString()
    : "—";

  return (
    <Card className={status.kill_switch ? "border-destructive/50 opacity-75" : ""}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Breakout monitor</CardTitle>
          <div className="flex items-center gap-2">
            {status.kill_switch ? (
              <Badge variant="destructive">kill switch</Badge>
            ) : status.market_open ? (
              <Badge variant="default">watching</Badge>
            ) : (
              <Badge variant="secondary">market closed</Badge>
            )}
          </div>
        </div>
        <CardDescription>
          {status.armed_tickets} armed ticket{status.armed_tickets !== 1 ? "s" : ""} ·{" "}
          {status.market_open ? "polling every 15 s" : "inactive outside market hours"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">
          Last tick: {lastTick}
        </span>
        <button
          onClick={toggleKillSwitch}
          disabled={toggling}
          className={`inline-flex h-8 items-center rounded-md px-3 text-xs font-medium transition-colors disabled:opacity-50 ${
            status.kill_switch
              ? "bg-primary text-primary-foreground hover:bg-primary/90"
              : "border border-destructive/50 text-destructive hover:bg-destructive/10"
          }`}
        >
          {status.kill_switch ? "Re-arm monitor" : "Kill switch"}
        </button>
      </CardContent>
    </Card>
  );
}
