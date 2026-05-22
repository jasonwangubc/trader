"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, ShieldAlert } from "lucide-react";
import type { ConcentrationResponse } from "@/lib/wheel";
import { money, pct } from "@/lib/wheel";
import { CorrelationReportView } from "./candidates-view";

export function ConcentrationPanel({ data }: { data: ConcentrationResponse | null }) {
  const [open, setOpen] = useState(false);

  if (!data || data.empty) {
    return (
      <div className="rounded-lg border bg-muted/30 p-3 mb-4 text-xs text-muted-foreground">
        No open wheel positions or stock holdings yet. Concentration analysis appears here after you log an option ticket or sync positions.
      </div>
    );
  }

  const report = data.report!;
  const totalWarnings =
    report.flagged_pairs.length + report.flagged_sectors.length + report.single_name_warnings.length;
  const total = report.total_notional;
  const opts = data.breakdown.filter(b => b.kind === "option").length;
  const stocks = data.breakdown.filter(b => b.kind === "stock").length;

  return (
    <div className="rounded-lg border bg-background mb-4">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-muted/50 transition-colors"
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <span className="font-semibold text-sm">Current exposure</span>
        <span className="text-muted-foreground text-xs">
          {opts} options · {stocks} stock holdings · {money(total, 0)}
        </span>
        {totalWarnings > 0 && (
          <span className="ml-auto flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
            <ShieldAlert className="h-3.5 w-3.5" />
            {totalWarnings} concentration warning{totalWarnings > 1 ? "s" : ""}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t px-4 py-3 text-xs">
          <CorrelationReportView report={report} />
        </div>
      )}
    </div>
  );
}
