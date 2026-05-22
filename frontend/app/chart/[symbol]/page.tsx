import Link from "next/link";
import { ArrowLeft, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { StockChart } from "@/components/stock-chart";
import { CompanySnapshot } from "@/components/company-snapshot";
import { FundamentalsPanel } from "@/components/fundamentals-panel";
import { RecommendationsPanel } from "@/components/recommendations-panel";
import { api, ApiError } from "@/lib/api";

interface ChartData {
  symbol: string;
  pivot: number | null;
  base_start: string | null;
  bars: Array<{ time: string; close: number; volume: number }>;
  sma50: Array<{ time: string; value: number }>;
  sma200: Array<{ time: string; value: number }>;
}

interface ScoreResult {
  symbol: string;
  tt_score: number;
  vcp_score: string;
  rs_rank: number | null;
  composite_score: string;
  sector: string | null;
}

export default async function ChartPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = await params;
  const sym = symbol.toUpperCase();

  interface RecStop { price: number; distance_pct: number }
  interface RecTarget { label: string; price: number; p_20d: number }
  interface RecData { recommended_stop: RecStop | null; targets: RecTarget[]; entry_price: number }

  let chartData: ChartData | null = null;
  let score: ScoreResult | null = null;
  let rec: RecData | null = null;
  let error: string | null = null;

  try {
    [chartData, score, rec] = await Promise.all([
      api<ChartData>(`/api/chart/${sym}`),
      api<ScoreResult[]>("/api/screener/results").then(rs => rs.find(r => r.symbol === sym) ?? null).catch(() => null),
      api<RecData>(`/api/chart/${sym}/recommendations`).catch(() => null),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? `${e.status}: ${e.message}` : String(e);
  }

  // Build chart overlays from recommendations
  const chartLevels = [];
  if (rec?.recommended_stop) {
    chartLevels.push({ price: rec.recommended_stop.price, label: `Stop -${rec.recommended_stop.distance_pct.toFixed(1)}%`, color: "#ef4444" });
  }
  if (rec?.targets) {
    const levelColors = ["#16a34a", "#22c55e", "#4ade80"];
    rec.targets.forEach((t, i) => {
      chartLevels.push({ price: t.price, label: `${t.label} ${Math.round(t.p_20d * 100)}%`, color: levelColors[i] ?? "#16a34a" });
    });
  }

  const lastBar = chartData?.bars.at(-1);
  const ma200Last = chartData?.sma200.at(-1);
  const pctAbove200 = lastBar && ma200Last
    ? ((lastBar.close - ma200Last.value) / ma200Last.value * 100).toFixed(1)
    : null;

  return (
    <main className="container mx-auto max-w-6xl p-6 sm:p-10">
      <div className="mb-4 flex items-center gap-3">
        <Link href="/screener" className="text-muted-foreground hover:text-foreground flex items-center gap-1 text-sm">
          <ArrowLeft className="h-3.5 w-3.5" /> Screener
        </Link>
      </div>

      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <h1 className="font-mono text-3xl font-bold">{sym}</h1>
          {score?.sector && <Badge variant="outline">{score.sector}</Badge>}
          {lastBar && (
            <span className="text-2xl font-semibold tabular-nums">
              ${lastBar.close.toFixed(2)}
            </span>
          )}
          {pctAbove200 && (
            <span className={`text-sm ${parseFloat(pctAbove200) > 0 ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"}`}>
              {parseFloat(pctAbove200) > 0 ? "+" : ""}{pctAbove200}% vs 200 SMA
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/tickets/new?symbol=${sym}${chartData?.pivot ? `&trigger=${chartData.pivot}` : ""}${rec?.recommended_stop ? `&stop=${rec.recommended_stop.price}` : ""}`}
            className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            <TrendingUp className="h-3.5 w-3.5" />
            Arm ticket{chartData?.pivot ? ` @ $${chartData.pivot.toFixed(2)}` : ""}
            {rec?.recommended_stop ? `, stop $${rec.recommended_stop.price.toFixed(2)}` : ""}
          </Link>
        </div>
      </header>

      {error && (
        <div className="border-destructive/50 bg-destructive/10 text-destructive mb-6 rounded-md border p-4 text-sm">{error}</div>
      )}

      {/* Scores row */}
      {score && (
        <div className="mb-4 flex gap-6 text-sm">
          <Stat label="Trend Template" value={`${score.tt_score}/8`} highlight={score.tt_score >= 6} />
          <Stat label="VCP score" value={`${(parseFloat(score.vcp_score) * 10).toFixed(1)}/10`} highlight={parseFloat(score.vcp_score) >= 0.6} />
          {score.rs_rank !== null && <Stat label="RS rank" value={String(score.rs_rank)} highlight={score.rs_rank >= 70} />}
          {chartData?.pivot && <Stat label="Pivot (buy point)" value={`$${chartData.pivot.toFixed(2)}`} highlight />}
          <Stat label="Composite" value={`${Math.round(parseFloat(score.composite_score) * 100)}/100`} />
        </div>
      )}

      {/* Company snapshot — TradingView-style overview */}
      <Card className="mb-4">
        <CardContent className="p-4">
          <CompanySnapshot symbol={sym} />
        </CardContent>
      </Card>

      {/* Main chart with stop/target overlays */}
      <Card className="mb-4">
        <CardContent className="p-4">
          <StockChart
            symbol={sym}
            height={480}
            showPivot
            levels={chartLevels.length > 0 ? chartLevels : undefined}
            className="w-full"
          />
          {chartLevels.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-red-500" />Stop</span>
              <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-emerald-600" />T1</span>
              <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-emerald-500" />T2</span>
              <span className="flex items-center gap-1"><span className="inline-block h-0.5 w-4 bg-emerald-400" />T3</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Stop / target recommendations */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-base">Stop &amp; target recommendations</CardTitle>
          <CardDescription className="text-xs">
            ATR-based and base-low stops · R-multiple targets · Monte Carlo probability estimates
            (10 000 simulated price paths using {sym}&apos;s historical volatility)
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RecommendationsPanel symbol={sym} />
        </CardContent>
      </Card>

      {/* Fundamentals panel */}
      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-base">Fundamentals</CardTitle>
          <CardDescription className="text-xs">
            Quarterly EPS + revenue · Minervini targets: EPS ≥+25% YoY (accelerating), Revenue ≥+25%, ROE ≥17%
          </CardDescription>
        </CardHeader>
        <CardContent>
          <FundamentalsPanel symbol={sym} />
        </CardContent>
      </Card>

      {/* Pivot explanation */}
      {chartData?.pivot && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Buy point analysis</CardTitle>
          </CardHeader>
          <CardContent className="text-sm space-y-2">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Detected pivot (breakout level)</span>
              <span className="font-mono font-semibold">${chartData.pivot.toFixed(2)}</span>
            </div>
            {lastBar && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Distance from current price</span>
                <span className={`tabular-nums ${lastBar.close >= chartData.pivot ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground"}`}>
                  {lastBar.close >= chartData.pivot
                    ? `+${((lastBar.close / chartData.pivot - 1) * 100).toFixed(1)}% above — may be extended`
                    : `-${((1 - lastBar.close / chartData.pivot) * 100).toFixed(1)}% below — watching for breakout`}
                </span>
              </div>
            )}
            {chartData.base_start && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Base started</span>
                <span className="tabular-nums">{new Date(chartData.base_start).toLocaleDateString("en-CA")}</span>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </main>
  );
}

function Stat({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className={`font-semibold tabular-nums ${highlight ? "text-emerald-600 dark:text-emerald-400" : ""}`}>{value}</div>
    </div>
  );
}
