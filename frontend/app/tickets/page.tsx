import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { type Ticket, fmtMoney, fmtPct } from "@/lib/tickets";

const STATUS_VARIANTS: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  armed: "default",
  triggered: "default",
  filled: "default",
  draft: "secondary",
  cancelled: "outline",
  expired: "outline",
  stopped_out: "destructive",
  target_hit: "default",
};

const STATUS_ORDER = ["armed", "triggered", "filled", "draft", "target_hit", "stopped_out", "cancelled", "expired"];

export default async function TicketsPage() {
  const tickets = await api<Ticket[]>("/api/tickets");

  // Group by status, ordered.
  const grouped = new Map<string, Ticket[]>();
  for (const status of STATUS_ORDER) grouped.set(status, []);
  for (const t of tickets) {
    if (!grouped.has(t.status)) grouped.set(t.status, []);
    grouped.get(t.status)!.push(t);
  }

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Tickets</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Pre-trade tickets — every entry must pass through here.
          </p>
        </div>
        <Link
          href="/tickets/new"
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          + New ticket
        </Link>
      </header>

      {tickets.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-16 text-center">
            <div className="text-muted-foreground">No tickets yet.</div>
            <Link
              href="/tickets/new"
              className="text-primary text-sm font-medium hover:underline"
            >
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
                  {status} ({ts.length})
                </h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {ts.map((t) => (
                    <TicketCard key={t.id} ticket={t} />
                  ))}
                </div>
              </section>
            ))}
        </div>
      )}
    </main>
  );
}

function TicketCard({ ticket }: { ticket: Ticket }) {
  const variant = STATUS_VARIANTS[ticket.status] ?? "secondary";
  const reward = ticket.target_price
    ? ((parseFloat(ticket.target_price) - parseFloat(ticket.trigger_price)) /
        (parseFloat(ticket.trigger_price) - parseFloat(ticket.stop_price))).toFixed(2)
    : null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="font-mono text-base">{ticket.symbol}</CardTitle>
            <CardDescription className="text-xs">
              {ticket.setup_type} · {ticket.is_paper ? "paper" : "live"}
            </CardDescription>
          </div>
          <Badge variant={variant}>{ticket.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <Row label="Trigger" value={fmtMoney(ticket.trigger_price, ticket.currency)} />
        <Row label="Stop" value={fmtMoney(ticket.stop_price, ticket.currency)} />
        {ticket.target_price && (
          <Row
            label={`Target${reward ? ` (${reward}R)` : ""}`}
            value={fmtMoney(ticket.target_price, ticket.currency)}
          />
        )}
        <div className="border-t pt-2" />
        <Row label="Shares" value={ticket.position_size_shares.toLocaleString()} />
        <Row label="Position" value={fmtMoney(ticket.position_size_value, ticket.currency)} />
        <Row label="Risk" value={`${fmtMoney(ticket.risk_amount, ticket.currency)} (${fmtPct(ticket.risk_pct)})`} />
      </CardContent>
    </Card>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  );
}
