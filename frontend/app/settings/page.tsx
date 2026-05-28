import { UserButton } from "@clerk/nextjs";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { QuestradConnect } from "./questrade-connect";
import { BrokerHistorySync } from "./broker-history-sync";

// Never cache — must always reflect the live connection status.
export const dynamic = "force-dynamic";

export const metadata = { title: "Settings" };

interface ConnectionStatus { connected: boolean; message: string; has_token?: boolean }

export default async function SettingsPage() {
  let status: ConnectionStatus = { connected: false, message: "Not connected", has_token: false };
  try {
    status = await api<ConnectionStatus>("/api/settings/questrade");
  } catch (e) {
    if (e instanceof ApiError) {
      status = { connected: false, message: e.message, has_token: true };
    }
  }
  const isBroken = !status.connected && !!status.has_token;

  return (
    <main className="container mx-auto max-w-2xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Settings</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Account and broker configuration.
        </p>
      </header>

      <div className="space-y-6">
        {/* Account */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Your account</CardTitle>
            <CardDescription>Manage your login and profile.</CardDescription>
          </CardHeader>
          <CardContent>
            <UserButton showName />
          </CardContent>
        </Card>

        {/* Questrade connection */}
        <Card className={
          status.connected ? "border-emerald-400/50"
          : isBroken      ? "border-amber-400/50"
          : ""
        }>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base">Questrade</CardTitle>
                <CardDescription>Connect your Questrade account for live data and trading.</CardDescription>
              </div>
              <span className={`text-xs font-medium px-2 py-1 rounded-full ${
                status.connected
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                  : isBroken
                    ? "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                    : "bg-muted text-muted-foreground"
              }`}>
                {status.connected ? "Connected" : isBroken ? "Token broken" : "Not connected"}
              </span>
            </div>
          </CardHeader>
          <CardContent>
            {isBroken && status.message && (
              <div className="mb-3 rounded-md border border-amber-400/40 bg-amber-50 dark:bg-amber-950/20 p-3 text-xs text-amber-800 dark:text-amber-200">
                {status.message}
              </div>
            )}
            <QuestradConnect initialConnected={status.connected} />
          </CardContent>
        </Card>

        {/* Broker trade history — backfill manual trades */}
        {status.connected && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Broker trade history</CardTitle>
              <CardDescription>
                Pull every fill from Questrade and reconstruct your trades — including manual orders.
                Required for the journal to reflect what you actually traded.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <BrokerHistorySync />
            </CardContent>
          </Card>
        )}

        {/* How to get Questrade token */}
        <Card className="bg-muted/30">
          <CardHeader>
            <CardTitle className="text-sm">How to get your Questrade token</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground space-y-2">
            <ol className="list-decimal list-inside space-y-1">
              <li>Log into <a href="https://login.questrade.com" target="_blank" rel="noopener" className="text-primary hover:underline">login.questrade.com</a></li>
              <li>Go to <strong>My Apps</strong> (top right)</li>
              <li>Click <strong>Generate new token</strong></li>
              <li>Copy the token and paste it above</li>
            </ol>
            <p className="text-xs mt-3">
              Tokens auto-rotate on each use and are stored encrypted in your account.
              They expire after 30 days of inactivity.
            </p>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
