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

        <LinkedCard
          title="Accounts"
          subtitle="Questrade balances — TFSA, RRSP, RESP"
          href="/accounts"
        />
        <LinkedCard
          title="Tickets"
          subtitle="Pre-trade tickets with streak-scaled sizing"
          href="/tickets"
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

function LinkedCard({ title, subtitle, href }: { title: string; subtitle: string; href: string }) {
  return (
    <a href={href} className="block">
      <Card className="transition-colors hover:border-primary/50">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>{title}</CardTitle>
            <Badge variant="default">live</Badge>
          </div>
          <CardDescription>{subtitle}</CardDescription>
        </CardHeader>
      </Card>
    </a>
  );
}

function PlaceholderCard({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <Card className="opacity-60">
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
