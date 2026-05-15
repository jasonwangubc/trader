import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { type Account, type HouseholdData, type Ticket, fmtMoney, fmtPct } from "@/lib/tickets";
import { CancelButton } from "./cancel-button";
import { CloseForm } from "./close-form";
import { ExitPlanForm } from "./exit-plan-form";
import { PyramidForm } from "./pyramid-form";
import { StockChart } from "@/components/stock-chart";

const TRIGGER_DESCRIPTIONS: Record<string, string> = {
  price_above:             "Fires when intraday price crosses the trigger. A bracket order (entry + stop, atomic) is sent to Questrade.",
  price_above_with_volume: "Fires when intraday price crosses the trigger AND volume is elevated. A bracket order (entry + stop, atomic) is then sent to Questrade.",
  day_close_above:         "Fires only on a daily close above the trigger — avoids intraday fakeouts. A bracket order (entry + stop, atomic) is sent to Questrade.",
};

interface TicketOrder {
  id: string;
  intent: string;
  side: string;
  order_type: string;
  quantity: number;
  limit_price: string | null;
  stop_price: string | null;
  status: string;
  submitted_at: string | null;
  filled_at: string | null;
  questrade_order_id: string | null;
  fills: TicketFill[];
}

interface TicketFill {
  id: string;
  quantity: number;
  price: string;
  occurred_at: string;
}

interface TrailingSuggestion {
  open_r: number;
  new_stop: string | null;
  action: string;
  urgency: string;
  milestone_label: string;
}

interface TicketDetail extends Ticket {
  orders: TicketOrder[];
  exit_plan: { targets: Array<{ price: string; shares: number; label: string; hit: boolean }> } | null;
  trailing: TrailingSuggestion | null;
}

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

const OUTCOME_LABEL: Record<string, string> = {
  win: "Win",
  loss: "Loss",
  scratch: "Scratch",
};

export default async function TicketDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let ticket: TicketDetail | null = null;
  let account: Account | undefined;
  let error: string | null = null;

  try {
    const [t, household] = await Promise.all([
      api<TicketDetail>(`/api/tickets/${id}`),
      api<HouseholdData>("/api/accounts").catch(() => ({ accounts: [], household_equity: {} })),
    ]);
    ticket = t;
    account = household.accounts.find(a => a.id === t.account_id);
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  if (error || !ticket) {
    return (
      <main className="container mx-auto max-w-4xl p-6 sm:p-10">
        <div className="border-destructive/50 bg-destructive/10 text-destructive rounded-md border p-4 text-sm">
          {error ?? "Ticket not found"}
        </div>
      </main>
    );
  }

  const reward =
    ticket.target_price
      ? ((parseFloat(ticket.target_price) - parseFloat(ticket.trigger_price)) /
          (parseFloat(ticket.trigger_price) - parseFloat(ticket.stop_price))).toFixed(2)
      : null;

  const variant = STATUS_VARIANTS[ticket.status] ?? "secondary";

  return (
    <main className="container mx-auto max-w-4xl p-6 sm:p-10">
      <div className="mb-6 flex items-center gap-3">
        <Link href="/tickets" className="text-muted-foreground hover:text-foreground text-sm">
          ← Tickets
        </Link>
      </div>

      <header className="mb-8 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="font-mono text-3xl font-semibold tracking-tight">{ticket.symbol}</h1>
            <Badge variant={variant}>{ticket.status.replace(/_/g, " ")}</Badge>
            <Badge variant={ticket.is_paper ? "secondary" : "outline"} className={ticket.is_paper ? "" : "border-emerald-500/50 text-emerald-500"}>
              {ticket.is_paper ? "paper" : "live"}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1.5 text-sm">
            {ticket.setup_type.replace(/_/g, " ")}
            {account && (
              <span className="ml-2 font-medium text-foreground/80">
                · {account.type} #{account.questrade_account_id} ({ticket.currency})
              </span>
            )}
          </p>
        </div>
        {ticket.outcome && (
          <div className="text-right">
            <div className={`text-2xl font-bold ${
              ticket.outcome === "win" ? "text-emerald-600 dark:text-emerald-400"
              : ticket.outcome === "loss" ? "text-destructive"
              : "text-muted-foreground"
            }`}>
              {ticket.r_multiple ? `${parseFloat(ticket.r_multiple) > 0 ? "+" : ""}${ticket.r_multiple}R` : "—"}
            </div>
            <div className="text-muted-foreground text-xs">{OUTCOME_LABEL[ticket.outcome]}</div>
          </div>
        )}
      </header>

      {/* Chart */}
      <Card className="mb-4">
        <CardContent className="p-4">
          <StockChart symbol={ticket.symbol} height={380} showPivot={ticket.status === "armed"} className="w-full" />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Setup + mechanics */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Trade setup</CardTitle>
            <CardDescription>
              {TRIGGER_DESCRIPTIONS[ticket.trigger_type] ?? ticket.trigger_type.replace(/_/g, " ")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {/* Prices */}
            <Row label="Trigger (buy point)" value={fmtMoney(ticket.trigger_price, ticket.currency)} />
            <Row label="Stop (max loss)" value={fmtMoney(ticket.stop_price, ticket.currency)} />
            {ticket.target_price && (
              <Row
                label={`Target${reward ? ` (${reward}R)` : ""}`}
                value={fmtMoney(ticket.target_price, ticket.currency)}
              />
            )}

            {/* Order mechanics */}
            <div className="border-t pt-2 space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                What will happen
              </p>
              {ticket.is_paper ? (
                <>
                  <Row label="Entry" value="Simulated fill at trigger — no real order sent" />
                  <Row label="Stop"  value="Simulated — monitored by backend polling only" />
                </>
              ) : (
                <>
                  <p className="text-xs text-muted-foreground leading-relaxed">
                    A <strong>bracket order</strong> is submitted to Questrade as a single atomic call.
                    Both legs are live at the broker from the moment the bracket is accepted —
                    the stop is never naked, even if the backend goes offline.
                  </p>
                  <Row
                    label="Entry leg"
                    value={`Stop-limit buy: stop ${fmtMoney(ticket.trigger_price, ticket.currency)}, limit ${fmtMoney((parseFloat(ticket.trigger_price) * 1.005).toFixed(2), ticket.currency)} (0.5% ceiling)`}
                  />
                  <Row
                    label="Stop leg"
                    value={`GTC stop-market sell at ${fmtMoney(ticket.stop_price, ticket.currency)} — activates at Questrade the moment entry fills`}
                  />
                </>
              )}
            </div>

            {/* Sizing */}
            <div className="border-t pt-2 space-y-2">
              <Row label="Shares" value={ticket.position_size_shares.toLocaleString()} />
              <Row label="Position value" value={fmtMoney(ticket.position_size_value, ticket.currency)} />
              <Row label="Risk amount" value={`${fmtMoney(ticket.risk_amount, ticket.currency)} (${fmtPct(ticket.risk_pct)})`} />
              <Row label="Streak mult." value={`${ticket.streak_multiplier_at_creation}×`} />
            </div>
          </CardContent>
        </Card>

        {/* Timeline */}
        <Card>
          <CardHeader><CardTitle className="text-base">Timeline</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <TimeRow label="Armed" ts={ticket.armed_at} />
            <TimeRow label="Triggered" ts={ticket.triggered_at} />
            <TimeRow label="Filled" ts={ticket.filled_at} />
            <TimeRow label="Closed" ts={ticket.closed_at} />
            <TimeRow label="Expires" ts={ticket.expires_at} />
            {ticket.realized_pnl && (
              <>
                <div className="border-t pt-2" />
                <Row
                  label="Realized P/L"
                  value={fmtMoney(ticket.realized_pnl, ticket.currency)}
                  highlight={parseFloat(ticket.realized_pnl) >= 0 ? "green" : "red"}
                />
              </>
            )}
          </CardContent>
        </Card>

        {/* Thesis */}
        {ticket.thesis && (
          <Card className="lg:col-span-2">
            <CardHeader><CardTitle className="text-base">Thesis</CardTitle></CardHeader>
            <CardContent className="text-muted-foreground text-sm leading-relaxed">
              {ticket.thesis}
            </CardContent>
          </Card>
        )}

        {/* Orders & fills */}
        {ticket.orders && ticket.orders.length > 0 && (
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Orders</CardTitle>
              <CardDescription>All orders placed for this ticket.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {ticket.orders.map((o) => (
                <OrderRow key={o.id} order={o} currency={ticket!.currency} />
              ))}
            </CardContent>
          </Card>
        )}

        {/* Cancel armed/draft ticket */}
        {(ticket.status === "armed" || ticket.status === "draft") && (
          <Card className="lg:col-span-2 border-destructive/30">
            <CardHeader>
              <CardTitle className="text-base">Cancel ticket</CardTitle>
              <CardDescription>
                Remove this ticket from the watchlist. This cannot be undone.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <CancelButton ticketId={ticket.id} symbol={ticket.symbol} />
            </CardContent>
          </Card>
        )}

        {/* Trailing stop suggestion */}
        {ticket.trailing && ticket.status === "filled" && (
          <Card className={`lg:col-span-2 ${
            ticket.trailing.urgency === "act"  ? "border-emerald-400 dark:border-emerald-700 bg-emerald-50/50 dark:bg-emerald-950/20" :
            ticket.trailing.urgency === "warn" ? "border-amber-400 dark:border-amber-700 bg-amber-50/50 dark:bg-amber-950/20" : ""
          }`}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">
                  {ticket.trailing.urgency === "act"  ? "🎯 " :
                   ticket.trailing.urgency === "warn" ? "⚠ " : ""}
                  {ticket.trailing.milestone_label}
                </CardTitle>
                <span className={`text-2xl font-bold tabular-nums ${
                  ticket.trailing.open_r >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                }`}>
                  {ticket.trailing.open_r > 0 ? "+" : ""}{ticket.trailing.open_r.toFixed(2)}R
                </span>
              </div>
              <CardDescription className="text-sm mt-1">{ticket.trailing.action}</CardDescription>
            </CardHeader>
            {ticket.trailing.new_stop && (
              <CardContent className="pt-0 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Suggested new stop</span>
                  <span className="font-mono font-semibold text-lg">
                    ${parseFloat(ticket.trailing.new_stop).toFixed(2)}
                  </span>
                </div>
                <p className="text-muted-foreground text-xs mt-1">
                  Based on last daily close. Update the stop order in Questrade manually, then record the new stop below.
                </p>
              </CardContent>
            )}
          </Card>
        )}

        {/* Exit ladder */}
        {ticket.status === "filled" && (
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Exit ladder</CardTitle>
              <CardDescription>
                Staged scale-out plan. Targets are tracked by the monitor — sell manually when hit.
                Default: 1/3 at +1.5R, 1/3 at +2.5R, 1/3 at +4R.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ExitPlanForm
                ticketId={ticket.id}
                currency={ticket.currency}
                totalShares={ticket.position_size_shares}
                triggerPrice={ticket.trigger_price}
                stopPrice={ticket.stop_price}
                currentPlan={ticket.exit_plan as any}
              />
            </CardContent>
          </Card>
        )}

        {/* Pyramiding */}
        {ticket.status === "filled" && (
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Add to position (pyramid)</CardTitle>
              <CardDescription>
                Record an add-on entry when the stock acts well. Blended cost basis is computed
                automatically. Stop stays fixed — it manages the full combined position.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <PyramidForm
                ticketId={ticket.id}
                currency={ticket.currency}
                currentShares={ticket.position_size_shares}
                stopPrice={ticket.stop_price}
              />
            </CardContent>
          </Card>
        )}

        {/* Manual exit */}
        {ticket.status === "filled" && (
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">Record exit</CardTitle>
              <CardDescription>
                If you closed this position manually, record the exit price here to update the
                streak and journal.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <CloseForm ticketId={ticket.id} currency={ticket.currency} />
            </CardContent>
          </Card>
        )}
      </div>
    </main>
  );
}

function Row({ label, value, highlight }: { label: string; value: string; highlight?: "green" | "red" }) {
  const cls = highlight === "green"
    ? "text-emerald-600 dark:text-emerald-400"
    : highlight === "red"
    ? "text-destructive"
    : "";
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className={`tabular-nums ${cls}`}>{value}</span>
    </div>
  );
}

function TimeRow({ label, ts }: { label: string; ts: string | null }) {
  if (!ts) return null;
  return (
    <div className="flex justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums text-xs">{new Date(ts).toLocaleString()}</span>
    </div>
  );
}

function OrderRow({ order, currency }: { order: TicketOrder; currency: string }) {
  const intentLabel: Record<string, string> = {
    entry: "Entry buy",
    stop_loss: "Stop loss",
    take_profit: "Take profit",
    exit: "Exit",
    scale_out: "Scale out",
  };
  const statusVariant: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
    filled: "default",
    submitted: "secondary",
    accepted: "secondary",
    cancelled: "outline",
    rejected: "destructive",
  };
  return (
    <div className="rounded-md border p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">{intentLabel[order.intent] ?? order.intent}</span>
        <Badge variant={statusVariant[order.status] ?? "secondary"}>{order.status}</Badge>
      </div>
      <div className="text-muted-foreground space-y-1 text-xs">
        <div className="flex justify-between">
          <span>{order.order_type.replace(/_/g, " ")} · {order.quantity.toLocaleString()} shares</span>
          {order.stop_price && <span>Stop @ {fmtMoney(order.stop_price, currency)}</span>}
          {order.limit_price && <span>Limit @ {fmtMoney(order.limit_price, currency)}</span>}
        </div>
        {order.fills?.map((f) => (
          <div key={f.id} className="flex justify-between">
            <span>Fill: {f.quantity.toLocaleString()} @ {fmtMoney(f.price, currency)}</span>
            <span>{new Date(f.occurred_at).toLocaleTimeString()}</span>
          </div>
        ))}
        {order.questrade_order_id && (
          <div className="text-muted-foreground/60">ID: {order.questrade_order_id}</div>
        )}
      </div>
    </div>
  );
}
