import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { type BuyingPower, type Position, type PositionsData } from "@/lib/positions";
import { type Account, type HouseholdData, fmtMoney } from "@/lib/tickets";
import { UnmanagedActions } from "./unmanaged-actions";

export const metadata = { title: 'Positions' };


export default async function PositionsPage() {
  let data: PositionsData | null = null;
  let accounts: Account[] = [];
  let equityByCurrency: Record<string, number> = {};
  let error: string | null = null;

  try {
    const [pos, household] = await Promise.all([
      api<PositionsData>("/api/positions"),
      api<HouseholdData>("/api/accounts"),
    ]);
    data = pos;
    accounts = household.accounts;
    equityByCurrency = Object.fromEntries(
      Object.entries(household.household_equity ?? {}).map(([k, v]) => [k, parseFloat(v)])
    );
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  const accountMap = new Map(accounts.map((a) => [a.id, a]));

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Positions</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Live holdings from Questrade. Cash-equivalents are flagged so you can free
            capital quickly.
          </p>
        </div>
        <Link
          href="/positions/sync"
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          Sync now
        </Link>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">
          {error}
        </div>
      )}

      {data && (
        <>
          <BuyingPowerSection breakdown={data.buying_power} />
          <PositionsSection
            positions={data.positions}
            accountMap={accountMap}
            equityByCurrency={equityByCurrency}
          />
        </>
      )}
    </main>
  );
}

function BuyingPowerSection({ breakdown }: { breakdown: BuyingPower[] }) {
  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>Buying power</CardTitle>
        <CardDescription>
          Cash + cash-equivalents you could deploy by EOD, per currency.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 sm:grid-cols-2">
        {breakdown.map((bp) => (
          <BuyingPowerCard key={bp.currency} bp={bp} />
        ))}
      </CardContent>
    </Card>
  );
}

function BuyingPowerCard({ bp }: { bp: BuyingPower }) {
  const equiv = parseFloat(bp.cash_equivalents);
  return (
    <div className="space-y-1">
      <div className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
        {bp.currency}
      </div>
      <div className="text-2xl font-semibold tabular-nums">
        {fmtMoney(bp.freeable_total, bp.currency)}
      </div>
      <div className="text-muted-foreground text-xs">
        Cash {fmtMoney(bp.cash, bp.currency)}
        {equiv > 0 && (
          <span> · parked {fmtMoney(bp.cash_equivalents, bp.currency)}</span>
        )}
      </div>
    </div>
  );
}

function PositionsSection({
  positions,
  accountMap,
  equityByCurrency,
}: {
  positions: Position[];
  accountMap: Map<string, Account>;
  equityByCurrency: Record<string, number>;
}) {
  if (positions.length === 0) {
    return (
      <Card>
        <CardContent className="text-muted-foreground py-12 text-center text-sm">
          No positions. Run a sync to pull them in.
        </CardContent>
      </Card>
    );
  }

  const cashEquivalents = positions.filter((p) => p.is_cash_equivalent);
  const buyAndHold      = positions.filter((p) => !p.is_cash_equivalent && p.is_buy_and_hold);
  const managed         = positions.filter((p) => !p.is_cash_equivalent && !p.is_buy_and_hold && p.ticket_id !== null);
  const unmanagedAll    = positions.filter((p) => !p.is_cash_equivalent && !p.is_buy_and_hold && p.ticket_id === null);
  const unmanagedNoStop = unmanagedAll.filter((p) => !p.broker_stop_price);
  const unmanagedStopped = unmanagedAll.filter((p) => !!p.broker_stop_price);

  return (
    <div className="space-y-6">
      {unmanagedNoStop.length > 0 && (
        <div className="border-destructive/40 bg-destructive/5 rounded-lg border p-4">
          <p className="text-destructive mb-3 text-xs font-semibold uppercase tracking-wide">
            ⚠ Unmanaged · no stop ({unmanagedNoStop.length}) — exposed without a defined exit
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {unmanagedNoStop.map((p) => (
              <PositionCard
                key={p.id} position={p}
                account={accountMap.get(p.account_id)}
                equityByCurrency={equityByCurrency}
              />
            ))}
          </div>
        </div>
      )}

      {unmanagedStopped.length > 0 && (
        <div className="border-amber-400/50 bg-amber-500/5 rounded-lg border p-4">
          <p className="text-amber-600 dark:text-amber-400 mb-3 text-xs font-semibold uppercase tracking-wide">
            Unmanaged · stop at broker ({unmanagedStopped.length}) — adopt to track in journal + risk
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {unmanagedStopped.map((p) => (
              <PositionCard
                key={p.id} position={p}
                account={accountMap.get(p.account_id)}
                equityByCurrency={equityByCurrency}
              />
            ))}
          </div>
        </div>
      )}

      {managed.length > 0 && (
        <PositionGroup title="Managed holdings" positions={managed} accountMap={accountMap} />
      )}
      {buyAndHold.length > 0 && (
        <PositionGroup
          title="Buy & hold"
          subtitle="Long-term positions you've explicitly opted out of risk tracking for."
          positions={buyAndHold}
          accountMap={accountMap}
        />
      )}
      {cashEquivalents.length > 0 && (
        <PositionGroup
          title="Cash-equivalents (parked capital)"
          subtitle="Sellable in a single trading day to free buying power."
          positions={cashEquivalents}
          accountMap={accountMap}
        />
      )}
    </div>
  );
}

function PositionGroup({
  title,
  subtitle,
  positions,
  accountMap,
}: {
  title: string;
  subtitle?: string;
  positions: Position[];
  accountMap: Map<string, Account>;
}) {
  return (
    <section>
      <div className="mb-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </h2>
        {subtitle && <p className="text-muted-foreground mt-1 text-xs">{subtitle}</p>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {positions.map((p) => (
          <PositionCard key={p.id} position={p} account={accountMap.get(p.account_id)} />
        ))}
      </div>
    </section>
  );
}

function PositionCard({
  position,
  account,
  equityByCurrency,
}: {
  position: Position;
  account?: Account;
  equityByCurrency?: Record<string, number>;
}) {
  const pnl = parseFloat(position.open_pnl);
  const pnlPct =
    parseFloat(position.avg_cost) > 0 && parseFloat(position.quantity) > 0
      ? (pnl / (parseFloat(position.avg_cost) * parseFloat(position.quantity))) * 100
      : null;
  const pnlClass =
    pnl > 0
      ? "text-emerald-600 dark:text-emerald-400"
      : pnl < 0
        ? "text-destructive"
        : "text-muted-foreground";

  const isUnmanaged = !position.is_cash_equivalent && !position.is_buy_and_hold && !position.ticket_id;
  const brokerStop   = position.broker_stop_price ? parseFloat(position.broker_stop_price) : null;
  const brokerTarget = position.broker_target_price ? parseFloat(position.broker_target_price) : null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="font-mono text-base">{position.symbol}</CardTitle>
            <CardDescription className="text-xs">
              {account ? `${account.type} · #${account.questrade_account_id}` : "—"}
            </CardDescription>
          </div>
          <div className="flex flex-col items-end gap-1">
            <Badge variant="outline" className="text-xs">
              {position.currency}
            </Badge>
            {position.is_cash_equivalent && (
              <Badge variant="secondary" className="text-xs">cash-eq</Badge>
            )}
            {position.is_buy_and_hold && (
              <Badge variant="secondary" className="text-xs">buy & hold</Badge>
            )}
            {position.ticket_id && (
              <Badge variant="default" className="text-xs">managed</Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-1.5 text-sm">
        <Row label="Qty" value={parseFloat(position.quantity).toLocaleString()} />
        <Row label="Avg cost" value={fmtMoney(position.avg_cost, position.currency)} />
        {position.current_price && (
          <Row label="Last" value={fmtMoney(position.current_price, position.currency)} />
        )}
        <Row label="Market value" value={fmtMoney(position.market_value, position.currency)} />
        <div className="border-t pt-2" />
        <div className="flex justify-between">
          <span className="text-muted-foreground">Open P/L</span>
          <span className={`tabular-nums ${pnlClass}`}>
            {fmtMoney(position.open_pnl, position.currency)}
            {pnlPct !== null && ` (${pnlPct.toFixed(2)}%)`}
          </span>
        </div>

        {/* Broker-side stop / target — surfaces "I do have a stop" without ticket */}
        {(brokerStop || brokerTarget) && (
          <div className="border-t pt-2 space-y-1 text-xs">
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70">At Questrade</div>
            {brokerStop && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Stop</span>
                <span className="tabular-nums text-amber-500">{fmtMoney(position.broker_stop_price!, position.currency)}</span>
              </div>
            )}
            {brokerTarget && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Target</span>
                <span className="tabular-nums text-emerald-500">{fmtMoney(position.broker_target_price!, position.currency)}</span>
              </div>
            )}
          </div>
        )}

        {isUnmanaged && (
          <UnmanagedActions
            position={position}
            account={account}
            equityByCurrency={equityByCurrency}
          />
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
