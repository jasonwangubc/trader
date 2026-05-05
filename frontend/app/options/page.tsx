import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { fmtMoney } from "@/lib/tickets";
import { NewOptionForm } from "./new-option-form";

interface OptionTicket {
  id: string;
  account_id: string;
  underlying_symbol: string;
  currency: string;
  strategy: string;
  option_type: string;
  strike_price: string;
  expiry_date: string;
  contracts: number;
  premium_received: string;
  total_premium: string;
  break_even: string | null;
  status: string;
  is_paper: boolean;
  thesis: string | null;
  premium_paid_to_close: string | null;
  realized_pnl: string | null;
  closed_at: string | null;
  created_at: string;
}

interface Account {
  id: string;
  questrade_account_id: string;
  type: string;
  primary_currency: string;
}

const STATUS_VARIANTS: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  open:     "default",
  closed:   "secondary",
  expired:  "outline",
  assigned: "destructive",
};

const STRATEGY_LABELS: Record<string, string> = {
  covered_call:     "Covered Call",
  cash_secured_put: "Cash-Secured Put",
  protective_put:   "Protective Put",
};

export default async function OptionsPage() {
  let tickets: OptionTicket[] = [];
  let accounts: Account[] = [];
  let error: string | null = null;

  try {
    const [opts, household] = await Promise.all([
      api<OptionTicket[]>("/api/options"),
      api<{ accounts: Account[] }>("/api/accounts"),
    ]);
    tickets = opts;
    accounts = household.accounts;
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  const open = tickets.filter(t => t.status === "open");
  const closed = tickets.filter(t => t.status !== "open");

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Options</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Covered calls and cash-secured puts — income and position entry.
        </p>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">
          {error}
        </div>
      )}

      <div className="grid gap-8 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-6">
          {open.length > 0 && (
            <section>
              <h2 className="text-muted-foreground mb-3 text-xs font-semibold uppercase tracking-wide">
                Open ({open.length})
              </h2>
              <div className="space-y-3">
                {open.map(t => <OptionCard key={t.id} ticket={t} />)}
              </div>
            </section>
          )}
          {closed.length > 0 && (
            <section>
              <h2 className="text-muted-foreground mb-3 text-xs font-semibold uppercase tracking-wide">
                Closed ({closed.length})
              </h2>
              <div className="space-y-3">
                {closed.map(t => <OptionCard key={t.id} ticket={t} />)}
              </div>
            </section>
          )}
          {tickets.length === 0 && (
            <Card>
              <CardContent className="text-muted-foreground py-12 text-center text-sm">
                No options tickets yet. Use the form to log a covered call or cash-secured put.
              </CardContent>
            </Card>
          )}
        </div>

        <aside>
          <NewOptionForm accounts={accounts} />
        </aside>
      </div>
    </main>
  );
}

function OptionCard({ ticket: t }: { ticket: OptionTicket }) {
  const variant = STATUS_VARIANTS[t.status] ?? "secondary";
  const expiry = new Date(t.expiry_date);
  const daysLeft = Math.ceil((expiry.getTime() - Date.now()) / 86_400_000);
  const pnl = t.realized_pnl ? parseFloat(t.realized_pnl) : null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="font-mono text-base">{t.underlying_symbol}</CardTitle>
            <CardDescription className="text-xs">
              {STRATEGY_LABELS[t.strategy] ?? t.strategy} · {t.is_paper ? "paper" : "live"}
            </CardDescription>
          </div>
          <Badge variant={variant}>{t.status}</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-1.5 text-sm">
        <Row label="Strike" value={fmtMoney(t.strike_price, t.currency)} />
        <Row label="Expiry" value={`${expiry.toLocaleDateString()} ${t.status === "open" ? `(${daysLeft}d)` : ""}`} />
        <Row label="Contracts" value={String(t.contracts)} />
        <Row label="Premium/share" value={fmtMoney(t.premium_received, t.currency)} />
        <Row label="Total premium" value={fmtMoney(t.total_premium, t.currency)} />
        {t.break_even && <Row label="Break-even" value={fmtMoney(t.break_even, t.currency)} />}
        {pnl !== null && (
          <div className="border-t pt-2">
            <Row label="Realized P/L" value={fmtMoney(t.realized_pnl!, t.currency)} />
          </div>
        )}
        {t.status === "open" && (
          <div className="border-t pt-2">
            <Link
              href={`/options/${t.id}/close`}
              className="text-primary text-xs hover:underline"
            >
              Record close / expiry →
            </Link>
          </div>
        )}
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
