import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { type Account, type HouseholdData, type Ticket, fmtMoney, fmtPct } from "@/lib/tickets";

export const metadata = { title: 'Tickets' };

const TRIGGER_LABELS: Record<string, string> = {
  price_above:              "Price crosses",
  price_above_with_volume:  "Price crosses with vol confirm",
  day_close_above:          "Day closes above",
};


const STATUS_VARIANTS: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  armed:       "default",
  triggered:   "default",
  filled:      "default",
  draft:       "secondary",
  cancelled:   "outline",
  expired:     "outline",
  stopped_out: "destructive",
  target_hit:  "default",
};

const STATUS_ORDER = ["armed", "triggered", "filled", "draft", "target_hit", "stopped_out", "cancelled", "expired"];

export default async function TicketsPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string }>;
}) {
  const { mode = "all" } = await searchParams;
  const [tickets, household] = await Promise.all([
    api<Ticket[]>("/api/tickets"),
    api<HouseholdData>("/api/accounts").catch(() => ({ accounts: [], household_equity: {} })),
  ]);
  const accountMap = new Map<string, Account>(household.accounts.map(a => [a.id, a]));

  const filtered =
    mode === "live"  ? tickets.filter(t => !t.is_paper) :
    mode === "paper" ? tickets.filter(t =>  t.is_paper) :
    tickets;

  const liveCount  = tickets.filter(t => !t.is_paper).length;
  const paperCount = tickets.filter(t =>  t.is_paper).length;

  const grouped = new Map<string, Ticket[]>();
  for (const status of STATUS_ORDER) grouped.set(status, []);
  for (const t of filtered) {
    if (!grouped.has(t.status)) grouped.set(t.status, []);
    grouped.get(t.status)!.push(t);
  }

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Tickets</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Every entry must be pre-committed through a ticket before it can fire.
          </p>
        </div>
        <Link
          href="/tickets/new"
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          + New ticket
        </Link>
      </header>

      {/* Mode filter tabs */}
      <div className="mb-6 flex items-center gap-1 border-b">
        {[
          { value: "all",   label: `All (${tickets.length})` },
          { value: "live",  label: `Live (${liveCount})` },
          { value: "paper", label: `Paper (${paperCount})` },
        ].map(({ value, label }) => (
          <Link
            key={value}
            href={value === "all" ? "/tickets" : `/tickets?mode=${value}`}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              mode === value || (value === "all" && mode !== "live" && mode !== "paper")
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>

      {filtered.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-16 text-center">
            <div className="text-muted-foreground">
              {mode === "live" ? "No live tickets yet. Enable real-money on an account and create a ticket with Live mode." : "No tickets yet."}
            </div>
            <Link href="/tickets/new" className="text-primary text-sm font-medium hover:underline">
              Create your first ticket →
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {Array.from(grouped.entries())
            .filter(([, ts]) => ts.length > 0)
            .map(([status, ts]) => (
              <section key={status}>
                <h2 className="text-muted-foreground mb-3 text-xs font-semibold uppercase tracking-wide">
                  {status.replace(/_/g, " ")} ({ts.length})
                </h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {ts.map((t) => <TicketCard key={t.id} ticket={t} account={accountMap.get(t.account_id)} />)}
                </div>
              </section>
            ))}
        </div>
      )}
    </main>
  );
}

function TicketCard({ ticket: t, account }: { ticket: Ticket; account?: Account }) {
  const variant = STATUS_VARIANTS[t.status] ?? "secondary";
  const trigger = parseFloat(t.trigger_price);
  const stop    = parseFloat(t.stop_price);
  const risk    = trigger - stop;
  const reward  = t.target_price && risk > 0
    ? ((parseFloat(t.target_price) - trigger) / risk).toFixed(1)
    : null;

  const outcomeColor =
    t.outcome === "win"  ? "text-emerald-600 dark:text-emerald-400" :
    t.outcome === "loss" ? "text-destructive" : "text-muted-foreground";

  // Entry order description
  const entryLimit  = (trigger * 1.005).toFixed(2);
  const entryOrder  = t.is_paper
    ? "Simulated fill at trigger"
    : `Stop-limit buy: stop ${fmtMoney(t.trigger_price, t.currency)} / limit ${fmtMoney(entryLimit, t.currency)}`;
  const stopOrder   = t.is_paper
    ? "Simulated stop"
    : `GTC stop-market sell at ${fmtMoney(t.stop_price, t.currency)}`;

  // Expiry — only relevant while armed
  const expiresIn = t.expires_at && t.status === "armed"
    ? Math.ceil((new Date(t.expires_at).getTime() - Date.now()) / 86_400_000)
    : null;

  return (
    <Link href={`/tickets/${t.id}`} className="block">
      <Card className="transition-colors hover:border-primary/50">
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-2">
            <div>
              <CardTitle className="font-mono text-base">{t.symbol}</CardTitle>
              <CardDescription className="text-xs">
                {t.setup_type.replace(/_/g, " ")}
                {account && ` · ${account.type} #${account.questrade_account_id}`}
              </CardDescription>
            </div>
            <div className="flex flex-col items-end gap-1">
              <Badge variant={variant}>{t.status.replace(/_/g, " ")}</Badge>
              <span className={`text-[10px] font-semibold uppercase ${t.is_paper ? "text-muted-foreground" : "text-emerald-500"}`}>
                {t.is_paper ? "paper" : "live"}
              </span>
              {t.r_multiple && (
                <span className={`text-xs font-semibold tabular-nums ${outcomeColor}`}>
                  {parseFloat(t.r_multiple) > 0 ? "+" : ""}{t.r_multiple}R
                </span>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-1.5 text-sm">
          {/* Prices */}
          <Row label="Trigger" value={fmtMoney(t.trigger_price, t.currency)} />
          <Row label="Stop"    value={fmtMoney(t.stop_price,    t.currency)} />
          {t.target_price && (
            <Row
              label={`Target${reward ? ` (${reward}R)` : ""}`}
              value={fmtMoney(t.target_price, t.currency)}
            />
          )}

          {/* Mechanics */}
          <div className="border-t pt-1.5 mt-1.5 space-y-1">
            <Row
              label="Fires when"
              value={`${TRIGGER_LABELS[t.trigger_type] ?? t.trigger_type} ${fmtMoney(t.trigger_price, t.currency)}`}
              muted
            />
            <Row label="Entry order" value={entryOrder} muted />
            <Row label="Stop order"  value={stopOrder}  muted />
            {expiresIn !== null && (
              <Row
                label="Expires"
                value={expiresIn > 0 ? `in ${expiresIn}d` : "today"}
                muted
                highlight={expiresIn <= 1}
              />
            )}
          </div>

          {/* Sizing */}
          <div className="border-t pt-1.5 mt-0.5 space-y-1.5">
            <Row label="Shares"   value={t.position_size_shares.toLocaleString()} />
            <Row label="Position" value={fmtMoney(t.position_size_value, t.currency)} />
            <Row label="Risk"     value={`${fmtMoney(t.risk_amount, t.currency)} (${fmtPct(t.risk_pct)})`} />
            {t.realized_pnl && (
              <Row label="P/L" value={fmtMoney(t.realized_pnl, t.currency)} />
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

function Row({ label, value, muted, highlight }: {
  label: string; value: string; muted?: boolean; highlight?: boolean;
}) {
  return (
    <div className={`flex justify-between gap-2 ${muted ? "text-xs" : "text-sm"}`}>
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className={`tabular-nums text-right ${highlight ? "text-amber-500 font-medium" : muted ? "text-muted-foreground" : ""}`}>
        {value}
      </span>
    </div>
  );
}
