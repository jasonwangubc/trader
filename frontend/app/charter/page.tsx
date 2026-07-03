import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { CharterEditor, CharterPerformance, CharterVersions } from "./charter-client";

export const metadata = { title: "Charter" };

export default function CharterPage() {
  return (
    <main className="container mx-auto max-w-5xl p-6 sm:p-10">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">Trading charter</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Your pre-committed rules — written before the drawdown, versioned so they can
          never be silently rewritten — and the honest scoreboard: your equity vs simply
          indexing the same deposits.
        </p>
      </header>

      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Performance vs benchmark</CardTitle>
            <CardDescription className="text-xs">
              Every deposit/withdrawal is replayed into a buy-and-hold benchmark
              (USD → SPY, CAD → ZSP.TO) at that day&apos;s adjusted close. Your equity line starts
              at the first account sync — the benchmark line spans the full cash-flow history.
              That gap is honest, not a bug.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CharterPerformance />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">The charter</CardTitle>
            <CardDescription className="text-xs">
              Versions are append-only. Revising requires a note explaining why, and the
              revision itself is audited — pre-commitment only works if the rules can&apos;t
              quietly change after a losing streak.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <CharterEditor />
            <CharterVersions />
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
