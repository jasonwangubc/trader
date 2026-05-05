export interface ScreenerSymbol {
  id: string;
  symbol: string;
  name: string | null;
  notes: string | null;
  is_active: boolean;
  created_at: string;
}

export interface ScoreResult {
  symbol: string;
  scored_at: string;
  sector: string | null;
  universe_source: string | null;
  tt_score: number;
  tt_criteria: Record<string, boolean>;
  vcp_score: string;
  vcp_details: Record<string, number | null>;
  rs_rank: number | null;
  rs_raw: string | null;
  last_close: string | null;
  ma_50: string | null;
  ma_150: string | null;
  ma_200: string | null;
  high_52w: string | null;
  low_52w: string | null;
  fundamental_score: string;
  revenue_growth: string | null;
  net_income_growth: string | null;
  net_margin: string | null;
  eps_ttm: string | null;
  composite_score: string;
}

export interface UniverseStats {
  total_symbols: number;
  symbols_with_bars: number;
  symbols_scored: number;
  last_scored_at: string | null;
}

export interface JournalSummary {
  total_trades: number;
  wins: number;
  losses: number;
  scratches: number;
  win_rate: number;
  avg_r_winner: number;
  avg_r_loser: number;
  expectancy: number;
  profit_factor: number;
  total_r: number;
  total_realized_pnl: number;
  by_setup: SetupBreakdown[];
  by_month: MonthBreakdown[];
  equity_curve: EquityPoint[];
}

export interface SetupBreakdown {
  setup_type: string;
  trades: number;
  wins: number;
  losses: number;
  scratches: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
}

export interface MonthBreakdown {
  month: string;
  trades: number;
  win_rate: number;
  avg_r: number;
  total_r: number;
}

export interface EquityPoint {
  date: string | null;
  symbol: string;
  r: number;
  cumulative_r: number;
}

export const TT_CRITERIA_LABELS: Record<string, string> = {
  price_above_150_200:   "Price > 150 & 200 MA",
  ma_150_above_200:      "150 MA > 200 MA",
  ma_200_trending_up:    "200 MA trending up",
  ma_50_above_150_200:   "50 MA > 150 & 200 MA",
  price_above_50:        "Price > 50 MA",
  pct_above_52w_low:     "25%+ above 52-week low",
  within_25pct_52w_high: "Within 25% of 52-week high",
  rs_outperforming:      "RS outperforming SPY",
};

export function fmtPct(v: string | number | null | undefined, decimals = 1): string {
  if (v == null) return "—";
  return `${(parseFloat(String(v)) * 100).toFixed(decimals)}%`;
}
