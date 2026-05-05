"use client";

import { useEffect, useRef, useState } from "react";
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type LineData,
  CrosshairMode,
  LineStyle,
  PriceScaleMode,
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
  mini?: boolean;          // compact sparkline mode (no axes, no RS panel)
  showPivot?: boolean;
  className?: string;
}

export function StockChart({ symbol, height = 420, mini = false, showPivot = true, className }: StockChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef     = useRef<IChartApi | null>(null);
  const [data, setData] = useState<ChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch chart data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_URL}/api/chart/${symbol}?days=${mini ? 126 : 504}`)
      .then(r => r.ok ? r.json() : r.json().then(d => Promise.reject(d.detail || "No data")))
      .then(d => { if (!cancelled) { setData(d); setLoading(false); } })
      .catch(e => { if (!cancelled) { setError(String(e)); setLoading(false); } });
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
      width: containerRef.current.clientWidth,
      height: mini ? height : height,
      layout: { background: { color: bg }, textColor },
      grid:   { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
      crosshair: { mode: mini ? CrosshairMode.Hidden : CrosshairMode.Normal },
      rightPriceScale: { borderColor: gridColor, visible: !mini },
      timeScale: { borderColor: gridColor, visible: !mini, rightOffset: 5, barSpacing: mini ? 2 : 6 },
      handleScroll: !mini,
      handleScale:  !mini,
    });
    chartRef.current = chart;

    // Volume (behind candles)
    const volSeries = chart.addHistogramSeries({
      color: isDark ? "#1e3a5f" : "#dbeafe",
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.80, bottom: 0 }, visible: false });
    volSeries.setData(data.bars.map(b => ({
      time: b.time as any,
      value: b.volume,
      color: b.close >= b.open
        ? (isDark ? "#166534" : "#bbf7d0")
        : (isDark ? "#7f1d1d" : "#fecaca"),
    })));

    // Candlesticks
    const candleSeries = chart.addCandlestickSeries({
      upColor: "#16a34a",   downColor: "#dc2626",
      borderUpColor: "#16a34a", borderDownColor: "#dc2626",
      wickUpColor: "#16a34a",   wickDownColor: "#dc2626",
      lastValueVisible: !mini,
      priceLineVisible: !mini,
    });
    candleSeries.setData(data.bars as CandlestickData[]);

    if (!mini) {
      // SMA overlays
      const smaConfig = [
        { data: data.sma50,  color: "#f59e0b", title: "50" },
        { data: data.sma150, color: "#8b5cf6", title: "150" },
        { data: data.sma200, color: "#ef4444", title: "200" },
      ];
      for (const { data: pts, color, title } of smaConfig) {
        if (pts.length === 0) continue;
        const s = chart.addLineSeries({
          color, lineWidth: 1, title,
          crosshairMarkerVisible: false,
          lastValueVisible: true,
          priceLineVisible: false,
        });
        s.setData(pts as LineData[]);
      }

      // Pivot line
      if (showPivot && data.pivot) {
        const pivotSeries = chart.addLineSeries({
          color: "#06b6d4",
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          title: `Pivot ${data.pivot.toFixed(2)}`,
          lastValueVisible: true,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        });
        // Draw pivot line across the full time range
        const times = data.bars.map(b => b.time);
        const startTime = data.base_start ?? times[Math.max(0, times.length - 65)];
        const endTime   = times[times.length - 1];
        pivotSeries.setData([
          { time: startTime as any, value: data.pivot },
          { time: endTime   as any, value: data.pivot },
        ]);
      }

      // RS line (on a separate right price scale, secondary pane feel)
      if (data.rs.length > 0) {
        const rsSeries = chart.addLineSeries({
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
        rsSeries.setData(data.rs as LineData[]);
      }
    }

    // Fit content
    chart.timeScale().fitContent();

    // Resize observer
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    if (containerRef.current) ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, [data, mini, showPivot, height]);

  if (loading) {
    return (
      <div style={{ height }} className={`flex items-center justify-center bg-muted/20 rounded-lg animate-pulse ${className ?? ""}`}>
        <span className="text-muted-foreground text-xs">Loading chart…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ height }} className={`flex items-center justify-center bg-muted/20 rounded-lg ${className ?? ""}`}>
        <span className="text-muted-foreground text-xs text-center px-4">{error}</span>
      </div>
    );
  }

  return (
    <div className={className}>
      {!mini && data?.pivot && (
        <div className="flex items-center gap-4 mb-2 text-xs text-muted-foreground px-1">
          <span><span className="inline-block w-3 h-0.5 bg-amber-400 mr-1" />50 SMA</span>
          <span><span className="inline-block w-3 h-0.5 bg-violet-500 mr-1" />150 SMA</span>
          <span><span className="inline-block w-3 h-0.5 bg-red-500 mr-1" />200 SMA</span>
          <span><span className="inline-block w-3 h-0.5 bg-cyan-500 mr-1 border-dashed" />Pivot {data.pivot.toFixed(2)}</span>
          <span className="ml-auto"><span className="inline-block w-3 h-0.5 bg-violet-400 mr-1" />RS line</span>
        </div>
      )}
      <div ref={containerRef} style={{ height }} className="rounded-lg overflow-hidden" />
    </div>
  );
}
