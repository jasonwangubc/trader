import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, type Health, type HealthDb, ApiError } from "@/lib/api";

type Status = { ok: boolean; detail: string };

async function probe(path: string): Promise<Status> {
  try {
    const res = await api<Health | HealthDb>(path);
    return { ok: res.status === "ok", detail: JSON.stringify(res) };
  } catch (e) {
    if (e instanceof ApiError) return { ok: false, detail: `${e.status} ${e.message}` };
    return { ok: false, detail: e instanceof Error ? e.message : String(e) };
  }
}

export default async function DashboardPage() {
  const [app, db] = await Promise.all([probe("/health"), probe("/health/db")]);

  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">trader</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Personal trading discipline tool. Sprint 1 scaffold.
        </p>
      </header>

      <section className="grid gap-4 sm:grid-cols-2">
        <StatusCard title="Backend" subtitle="FastAPI / app health" status={app} />
        <StatusCard title="Database" subtitle="Postgres via async session" status={db} />

        <PlaceholderCard
          title="Accounts"
          subtitle="Sprint 1 — Questrade OAuth + per-currency balances"
        />
        <PlaceholderCard
          title="Tickets"
          subtitle="Sprint 1 — pre-trade ticket form, sized from streak-scaled risk"
        />
        <PlaceholderCard
          title="Breakout monitor"
          subtitle="Sprint 2 — staged triggers + auto-place buy/stop on fire"
        />
        <PlaceholderCard
          title="Screener"
          subtitle="Sprint 3 — nightly Trend Template + lenient VCP"
        />
      </section>
    </main>
  );
}

function StatusCard({ title, subtitle, status }: { title: string; subtitle: string; status: Status }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <Badge variant={status.ok ? "default" : "destructive"}>
            {status.ok ? "ok" : "down"}
          </Badge>
        </div>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent>
        <code className="text-muted-foreground text-xs break-all">{status.detail}</code>
      </CardContent>
    </Card>
  );
}

function PlaceholderCard({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <Card className="opacity-70">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>{title}</CardTitle>
          <Badge variant="secondary">pending</Badge>
        </div>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
    </Card>
  );
}
