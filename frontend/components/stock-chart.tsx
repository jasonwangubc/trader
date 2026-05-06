"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  CrosshairMode,
  LineStyle,
} from "lightweight-charts";
import { API_URL } from "@/lib/api";

interface ChartBar  { time: string; open: number; high: number; low: number; close: number; volume: number }
interface ChartPoint { time: string; value: number }

interface ChartData {
  symbol: string;
  bars:   ChartBar[];
  sma50:  ChartPoint[];
  sma150: ChartPoint[];
  sma200: ChartPoint[];
  rs:     ChartPoint[];
  pivot:  number | null;
  base_start: string | null;
}

interface StockChartProps {
  symbol: string;
  height?: number;
  mini?: boolean;          // compact sparkline mode — fewer overlays, no axes
  showPivot?: boolean;
  showSmas?: boolean;      // show 50/150 SMA in mini mode (default true)
  className?: string;
}

export function StockChart({ symbol, height = 420, mini = false, showPivot = true, showSmas = true, className }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const [data, setData]       = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  // Fetch data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // Mini needs enough bars for SMA150 — fetch 252 (1yr)
    fetch(`${API_URL}/api/chart/${symbol}?days=${mini ? 252 : 504}`)
      .then(r => r.ok ? r.json() : r.json().then((d: any) => Promise.reject(d.detail ?? "No data")))
      .then((d: ChartData) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch((e: unknown) => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [symbol, mini]);

  // Build chart
  useEffect(() => {
    if (!containerRef.current || !data || data.bars.length === 0) return;

    const isDark = document.documentElement.classList.contains("dark");
    const textColor = isDark ? "#9ca3af" : "#6b7280";
    const gridColor = isDark ? "#1f2937" : "#f3f4f6";
    const bg        = isDark ? "#0f172a" : "#ffffff";

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height,
      layout: { background: { color: bg }, textColor },
      grid: {
        vertLines: { color: mini ? "transparent" : gridColor },
        horzLines: { color: mini ? "transparent" : gridColor },
      },
      crosshair: { mode: mini ? CrosshairMode.Hidden : CrosshairMode.Normal },
      rightPriceScale: { borderColor: gridColor, visible: !mini },
      timeScale: { borderColor: gridColor, visible: !mini, rightOffset: 5, barSpacing: mini ? 2 : 6 },
      handleScroll: !mini,
      handleScale:  !mini,
    });
    chartRef.current = chart;

    // ── Volume histogram ──────────────────────────────────────────────────────
    const volSeries = chart.addSeries(HistogramSeries, {
      color: isDark ? "#1e3a5f" : "#dbeafe",
      priceFormat: { type: "volume" as const },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({
      scaleMargins: { top: 0.80, bottom: 0 },
      visible: false,
    });
    volSeries.setData(
      data.bars.map(b => ({
        time: b.time as any,
        value: b.volume,
        color: b.close >= b.open
          ? (isDark ? "#166534" : "#bbf7d0")
          : (isDark ? "#7f1d1d" : "#fecaca"),
      }))
    );

    // ── Candlesticks ─────────────────────────────────────────────────────────
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor:          "#16a34a",
      downColor:        "#dc2626",
      borderUpColor:    "#16a34a",
      borderDownColor:  "#dc2626",
      wickUpColor:      "#16a34a",
      wickDownColor:    "#dc2626",
      lastValueVisible: !mini,
      priceLineVisible: !mini,
    });
    candleSeries.setData(data.bars as any[]);

    // ── SMA overlays — full chart: all three; mini: 50 + 150 ─────────────────
    if (showSmas) {
      const smaConfig = mini
        ? [
            { pts: data.sma50,  color: "#f59e0b", title: "" },
            { pts: data.sma150, color: "#8b5cf6", title: "" },
          ]
        : [
            { pts: data.sma50,  color: "#f59e0b", title: "50"  },
            { pts: data.sma150, color: "#8b5cf6", title: "150" },
            { pts: data.sma200, color: "#ef4444", title: "200" },
          ];
      for (const { pts, color, title } of smaConfig) {
        if (!pts.length) continue;
        const s = chart.addSeries(LineSeries, {
          color,
          lineWidth: mini ? 1 : 1,
          title,
          crosshairMarkerVisible: false,
          lastValueVisible: !mini,
          priceLineVisible: false,
        });
        s.setData(pts as any[]);
      }
    }

    if (!mini) {
      // ── Pivot line ─────────────────────────────────────────────────────────
      if (showPivot && data.pivot) {
        const pivotS = chart.addSeries(LineSeries, {
          color: "#06b6d4",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: `Pivot ${data.pivot.toFixed(2)}`,
          lastValueVisible: true,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        const times = data.bars.map(b => b.time);
        const startTime = data.base_start ?? times[Math.max(0, times.length - 65)];
        const endTime   = times[times.length - 1];
        pivotS.setData([
          { time: startTime as any, value: data.pivot },
          { time: endTime   as any, value: data.pivot },
        ]);
      }

      // ── RS line (vs SPY) ───────────────────────────────────────────────────
      if (data.rs.length > 0) {
        const rsSeries = chart.addSeries(LineSeries, {
          color: "#a78bfa",
          lineWidth: 1,
          priceScaleId: "rs",
          title: "RS",
          crosshairMarkerVisible: false,
          lastValueVisible: true,
          priceLineVisible: false,
        });
        chart.priceScale("rs").applyOptions({
          scaleMargins: { top: 0.85, bottom: 0.0 },
          visible: false,
        });
        rsSeries.setData(data.rs as any[]);
      }
    }

    chart.timeScale().fitContent();

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [data, mini, showPivot, height]);

  if (loading) {
    return (
      <div
        style={{ height }}
        className={`flex items-center justify-center rounded-lg bg-muted/20 animate-pulse ${className ?? ""}`}
      >
        <span className="text-muted-foreground text-xs">Loading chart…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{ height }}
        className={`flex items-center justify-center rounded-lg bg-muted/20 ${className ?? ""}`}
      >
        <span className="text-muted-foreground text-center px-4 text-xs">{error}</span>
      </div>
    );
  }

  return (
    <div className={className}>
      {!mini && data?.pivot && (
        <div className="mb-2 flex items-center gap-4 px-1 text-xs text-muted-foreground">
          <span><span className="mr-1 inline-block h-0.5 w-3 bg-amber-400" />50 SMA</span>
          <span><span className="mr-1 inline-block h-0.5 w-3 bg-violet-500" />150 SMA</span>
          <span><span className="mr-1 inline-block h-0.5 w-3 bg-red-500" />200 SMA</span>
          <span><span className="mr-1 inline-block h-0.5 w-3 border-dashed bg-cyan-500" />Pivot {data.pivot.toFixed(2)}</span>
          <span className="ml-auto"><span className="mr-1 inline-block h-0.5 w-3 bg-violet-400" />RS line</span>
        </div>
      )}
      <div ref={containerRef} style={{ height }} className="overflow-hidden rounded-lg" />
    </div>
  );
}
