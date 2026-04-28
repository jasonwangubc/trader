import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";

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
}

function fmt(value: string, currency: string) {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
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

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Accounts</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Live balances from Questrade — synced on page load.
          </p>
        </div>
        <SyncButton />
      </header>

      {error && (
        <div className="mb-6 rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      )}

      {data && (
        <>
          <HouseholdSummary equity={data.household_equity} />
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {data.accounts.map((account) => (
              <AccountCard key={account.id} account={account} />
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

function AccountCard({ account }: { account: Account }) {
  const usd = account.balances.find((b) => b.currency === "USD");
  const cad = account.balances.find((b) => b.currency === "CAD");
  const primary = account.balances.find((b) => b.currency === account.primary_currency);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">
            {account.nickname ?? account.type}
          </CardTitle>
          <Badge variant={account.real_money_enabled ? "destructive" : "secondary"}>
            {account.real_money_enabled ? "live" : "paper"}
          </Badge>
        </div>
        <CardDescription>#{account.questrade_account_id}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {[cad, usd].filter(Boolean).map((b) => (
          <div key={b!.currency} className="space-y-1">
            <div className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
              {b!.currency}
            </div>
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
      </CardContent>
    </Card>
  );
}

// Client component just for the sync button — lives in its own file to keep this page a Server Component.
function SyncButton() {
  return (
    <a
      href="/accounts/sync"
      className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
    >
      Sync now
    </a>
  );
}
