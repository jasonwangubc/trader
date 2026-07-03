import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { ActiveAccountButton } from "./active-account-button";
import { LiveToggle } from "./live-toggle";

export const metadata = { title: 'Accounts' };


interface Balance {
  currency: string;
  cash: string;
  market_value: string;
  total_equity: string;
  buying_power: string;
}

interface Account {
  id: string;
  questrade_account_id: string;
  type: string;
  primary_currency: string;
  nickname: string | null;
  real_money_enabled: boolean;
  balances: Balance[];
}

interface HouseholdData {
  accounts: Account[];
  household_equity: Record<string, string>;
  active_account_id: string | null;
}

function fmt(value: string, currency: string) {
  return new Intl.NumberFormat("en-CA", {
    style: "currency", currency,
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(parseFloat(value));
}

export default async function AccountsPage() {
  let data: HouseholdData | null = null;
  let error: string | null = null;

  try {
    data = await api<HouseholdData>("/api/accounts");
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  const anyLive = data?.accounts.some(a => a.real_money_enabled) ?? false;

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Accounts</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Questrade balances — data always reflects real holdings.
          </p>
        </div>
        <Link
          href="/accounts/sync"
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          Sync now
        </Link>
      </header>

      {/* Mode explanation */}
      <div className={`mb-6 rounded-lg border px-4 py-3 text-sm ${anyLive ? "border-destructive/40 bg-destructive/5" : "border-muted bg-muted/30"}`}>
        <p className="font-medium mb-1">
          {anyLive
            ? "⚠ Real-money execution is ENABLED on one or more accounts"
            : "Paper mode — all ticket execution is simulated (no real orders sent to Questrade)"}
        </p>
        <p className="text-muted-foreground text-xs">
          Position data always comes from your live Questrade accounts regardless of this setting.
          Enabling live execution means the monitor will place real buy and stop-loss orders when a ticket triggers.
        </p>
      </div>

      {error && (
        <div className="mb-6 rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">{error}</div>
      )}

      {data && (
        <>
          <HouseholdSummary equity={data.household_equity} />
          {!data.active_account_id && data.accounts.length > 1 && (
            <div className="mt-4 rounded-md border border-amber-500/40 bg-amber-500/5 px-4 py-3 text-sm">
              <span className="font-medium">No trading account set.</span>{" "}
              <span className="text-muted-foreground">
                Sizing and risk currently count every account below. Pick the one account you
                actually trade (e.g. your RRSP) so the rest stay out of the math.
              </span>
            </div>
          )}
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.accounts.map((account) => (
              <AccountCard
                key={account.id}
                account={account}
                isActive={account.id === data.active_account_id}
                anyActive={data.active_account_id !== null}
              />
            ))}
          </div>
        </>
      )}
    </main>
  );
}

function HouseholdSummary({ equity }: { equity: Record<string, string> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Household total equity</CardTitle>
        <CardDescription>Sum across all active accounts, per currency</CardDescription>
      </CardHeader>
      <CardContent className="flex gap-8">
        {Object.entries(equity).map(([currency, amount]) => (
          <div key={currency}>
            <div className="text-muted-foreground text-xs font-medium uppercase">{currency}</div>
            <div className="text-2xl font-semibold tabular-nums">{fmt(amount, currency)}</div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function AccountCard({
  account,
  isActive,
  anyActive,
}: {
  account: Account;
  isActive: boolean;
  anyActive: boolean;
}) {
  const usd = account.balances.find((b) => b.currency === "USD");
  const cad = account.balances.find((b) => b.currency === "CAD");
  const dimmed = anyActive && !isActive;

  return (
    <Card
      className={`${account.real_money_enabled ? "border-destructive/40" : ""} ${
        isActive ? "border-primary/60 ring-1 ring-primary/30" : dimmed ? "opacity-60" : ""
      }`}
    >
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-base">{account.nickname ?? account.type}</CardTitle>
            <CardDescription>#{account.questrade_account_id}</CardDescription>
          </div>
          <div className="flex gap-1">
            {isActive && <Badge>ACTIVE</Badge>}
            <Badge variant={account.real_money_enabled ? "destructive" : "secondary"}>
              {account.real_money_enabled ? "LIVE" : "paper"}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {[cad, usd].filter(Boolean).map((b) => (
          <div key={b!.currency} className="space-y-1">
            <div className="text-muted-foreground text-xs font-medium uppercase tracking-wide">{b!.currency}</div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total equity</span>
              <span className="font-semibold tabular-nums">{fmt(b!.total_equity, b!.currency)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Cash</span>
              <span className="tabular-nums">{fmt(b!.cash, b!.currency)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Market value</span>
              <span className="tabular-nums">{fmt(b!.market_value, b!.currency)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Buying power</span>
              <span className="tabular-nums">{fmt(b!.buying_power, b!.currency)}</span>
            </div>
          </div>
        ))}

        {/* Trading-account scope */}
        <div className="border-t pt-3">
          <ActiveAccountButton
            accountId={account.id}
            isActive={isActive}
            anyActive={anyActive}
          />
        </div>

        {/* Live toggle */}
        <div className="border-t pt-3">
          <LiveToggle
            accountId={account.id}
            currentLive={account.real_money_enabled}
            accountType={account.type}
            accountNumber={account.questrade_account_id}
          />
        </div>
      </CardContent>
    </Card>
  );
}
