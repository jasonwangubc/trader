import { UserButton } from "@clerk/nextjs";
import { api, ApiError } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { QuestradConnect } from "./questrade-connect";

// Never cache — must always reflect the live connection status.
export const dynamic = "force-dynamic";

export const metadata = { title: "Settings" };

interface ConnectionStatus { connected: boolean; message: string }

export default async function SettingsPage() {
  let status: ConnectionStatus = { connected: false, message: "" };
  try {
    status = await api<ConnectionStatus>("/api/settings/questrade");
  } catch {/* not connected */}

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
        <Card className={status.connected ? "border-emerald-400/50" : ""}>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base">Questrade</CardTitle>
                <CardDescription>Connect your Questrade account for live data and trading.</CardDescription>
              </div>
              <span className={`text-xs font-medium px-2 py-1 rounded-full ${
                status.connected
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                  : "bg-muted text-muted-foreground"
              }`}>
                {status.connected ? "Connected" : "Not connected"}
              </span>
            </div>
          </CardHeader>
          <CardContent>
            <QuestradConnect initialConnected={status.connected} />
          </CardContent>
        </Card>

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
