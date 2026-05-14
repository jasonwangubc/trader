"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  BarSeries,
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

interface PriceLevel {
  price: number;
  label: string;
  color: string;
}

interface StockChartProps {
  symbol: string;
  height?: number;
  mini?: boolean;          // compact sparkline mode — fewer overlays, no axes
  days?: number;           // explicit fetch window; overrides the mini/full default
  visibleDays?: number;    // initial visible window after fitContent (zooms in tighter)
  showPivot?: boolean;
  showSmas?: boolean;      // show 50/150 SMA in mini mode (default true)
  levels?: PriceLevel[];  // optional horizontal price lines (stop, targets, etc.)
  className?: string;
  barSpacing?: number;     // wider candles when zoomed; defaults handle most cases
}

export function StockChart({
  symbol,
  height = 420,
  mini = false,
  days,
  visibleDays,
  showPivot = true,
  showSmas = true,
  levels,
  className,
  barSpacing,
}: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const [data, setData]       = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  // Fetch data — caller can override; otherwise mini=252 bars (1yr), full=504 (2yr)
  const fetchDays = days ?? (mini ? 252 : 504);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/api/chart/${symbol}?days=${fetchDays}`)
      .then(r => r.json().then((d: any) => {
        if (!r.ok) return Promise.reject(d.detail ?? `HTTP ${r.status}`);
        return d as ChartData;
      }))
      .then((d: ChartData) => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch((e: unknown) => { if (!cancelled) { setError(String(e)); setLoading(false); } });
    return () => { cancelled = true; };
  }, [symbol, fetchDays]);

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
      timeScale: {
        borderColor: gridColor,
        visible: !mini,
        rightOffset: mini ? 3 : 12,
        barSpacing: barSpacing ?? (mini ? 2 : 6),
      },
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

    // ── OHLC bars ─────────────────────────────────────────────────────────────
    const ohlcSeries = chart.addSeries(BarSeries, {
      upColor:          "#16a34a",
      downColor:        "#dc2626",
      openVisible:      false,
      lastValueVisible: !mini,
      priceLineVisible: !mini,
    });
    ohlcSeries.setData(data.bars as any[]);

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
        const times     = data.bars.map(b => b.time);
        const endTime   = times[times.length - 1];
        const rawStart  = data.base_start ?? times[Math.max(0, times.length - 65)];
        // lightweight-charts requires strictly ascending time — if base_start equals
        // the last bar (stock at recent high), step back one bar to avoid the error.
        const startTime = rawStart < endTime
          ? rawStart
          : times[Math.max(0, times.length - 2)];
        if (startTime < endTime) {
          pivotS.setData([
            { time: startTime as any, value: data.pivot },
            { time: endTime   as any, value: data.pivot },
          ]);
        }
      }

      // ── Custom price levels (stop, targets, etc.) ─────────────────────────
      if (levels && levels.length > 0 && data.bars.length >= 2) {
        const times = data.bars.map(b => b.time);
        const t0 = times[Math.max(0, times.length - 65)] as any;
        const t1 = times[times.length - 1] as any;
        for (const level of levels) {
          const ls = chart.addSeries(LineSeries, {
            color: level.color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            title: level.label,
            lastValueVisible: true,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
          });
          ls.setData([
            { time: t0, value: level.price },
            { time: t1, value: level.price },
          ]);
        }
      }

      // ── RS line (vs SPY) ───────────────────────────────────────────────────
      if (data.rs.length > 0) {
        const rsSeries = chart.addSeries(LineSeries, {
          color: "#a78bfa",
          lineWidth: 1,
          priceScaleId: "rs",
          title: "RS (indexed)",
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

    // Zoom in to the last `visibleDays` bars if requested (clearer recent action)
    if (visibleDays && data.bars.length > visibleDays) {
      const fromIdx = data.bars.length - visibleDays;
      chart.timeScale().setVisibleRange({
        from: data.bars[fromIdx].time as any,
        to:   data.bars[data.bars.length - 1].time as any,
      });
    }

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
  }, [data, mini, showPivot, height, visibleDays, barSpacing, levels]);

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
          <span className="ml-auto"><span className="mr-1 inline-block h-0.5 w-3 bg-violet-400" />RS vs SPY (indexed 100)</span>
        </div>
      )}
      <div ref={containerRef} style={{ height }} className="overflow-hidden rounded-lg" />
    </div>
  );
}
