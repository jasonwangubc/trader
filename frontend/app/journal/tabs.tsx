"use client";

import { useState } from "react";
import { BrokerJournalTab } from "./broker-tab";

export function JournalTabs({ ticketView }: { ticketView: React.ReactNode }) {
  const [tab, setTab] = useState<"broker" | "tickets">("broker");

  return (
    <div className="space-y-4">
      <div className="border-b">
        <div className="flex gap-1">
          <TabButton active={tab === "broker"} onClick={() => setTab("broker")}>
            From broker <span className="text-muted-foreground ml-1 text-xs">(real fills)</span>
          </TabButton>
          <TabButton active={tab === "tickets"} onClick={() => setTab("tickets")}>
            From tickets <span className="text-muted-foreground ml-1 text-xs">(in-app only)</span>
          </TabButton>
        </div>
      </div>
      {tab === "broker" ? <BrokerJournalTab /> : <>{ticketView}</>}
    </div>
  );
}

function TabButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
        active
          ? "border-primary text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}
