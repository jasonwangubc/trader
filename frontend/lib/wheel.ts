// Shared types for the wheel feature.

export interface WheelCandidate {
  id: string;
  symbol: string;
  sector: string | null;
  strategy: "csp" | "cc";
  last_price: string;
  expiry: string;
  dte: number;
  strike: string;
  option_type: "put" | "call";
  bid: string | null;
  ask: string | null;
  mid: string;
  last: string | null;
  bid_ask_spread_pct: string | null;
  open_interest: number;
  volume: number;
  implied_volatility: string | null;
  delta_approx: string | null;
  premium_yield_pct: string;
  annualized_yield_pct: string;
  otm_pct: string;
  capital_at_risk: string;
  breakeven: string;
  earnings_before_expiry: boolean;
  next_earnings_date: string | null;
  score: string;
  score_breakdown: Record<string, number>;
  scanned_at: string;
}

export interface ScanStatus {
  running: boolean;
  scanned?: number;
  with_data?: number;
  candidates?: number;
  started_at?: string;
  finished_at?: string;
  duration_seconds?: number;
  error?: string;
}

export interface CorrelationPair {
  a: string;
  b: string;
  correlation: number;
  overlap_days: number;
}

export interface SectorBucket {
  sector: string;
  symbols: string[];
  notional: number;
  pct_of_total: number;
}

export interface CorrelationReport {
  symbols: string[];
  total_notional: number;
  pairs: CorrelationPair[];
  sectors: SectorBucket[];
  flagged_pairs: CorrelationPair[];
  flagged_sectors: SectorBucket[];
  single_name_warnings: Array<{ symbol: string; pct_of_total: number; notional: number }>;
}

export interface ConcentrationResponse {
  empty: boolean;
  breakdown: Array<{
    kind: "option" | "stock";
    symbol: string;
    strategy?: string;
    contracts?: number;
    quantity?: number;
    notional: number;
  }>;
  report: CorrelationReport | null;
}

export function pct(s: string | number | null | undefined, digits = 1): string {
  if (s === null || s === undefined) return "—";
  const f = typeof s === "number" ? s : parseFloat(s);
  if (!isFinite(f)) return "—";
  return `${(f * 100).toFixed(digits)}%`;
}

export function money(s: string | number | null | undefined, digits = 2): string {
  if (s === null || s === undefined) return "—";
  const f = typeof s === "number" ? s : parseFloat(s);
  if (!isFinite(f)) return "—";
  return `$${f.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function num(s: string | number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const f = typeof s === "number" ? s : parseFloat(s);
  if (!isFinite(f)) return "—";
  return f.toLocaleString("en-US");
}
