export interface ResultsPage {
  items: ScoreResult[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

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
  net_income_growth: string | null;        // most recent quarter YoY EPS growth
  earnings_annual_growth: string | null;   // TTM/annual YoY (compare vs quarterly for acceleration)
  net_margin: string | null;
  roe: string | null;
  eps_ttm: string | null;
  eps_rank: number | null;
  smr_rank: number | null;

  // Pattern + buyability — what kind of setup is this and how actionable
  pattern_type: string | null;             // 'vcp' | 'cwh' | 'flat_base' | 'high_tight_flag' | null
  pattern_quality: string | null;          // 0-1 quality of matched pattern
  buyability: string | null;               // 'at_pivot' | 'in_base' | 'extended' | 'broken' | 'no_pattern'
  pivot_price: string | null;
  base_low: string | null;
  base_length_days: number | null;
  base_depth_pct: string | null;
  extension_pct: string | null;            // % current price is past pivot

  composite_score: string;
  // ML ranker: calibrated probability (0-1, as string) that the setup reaches
  // a 2R target before its stop after a breakout. null = no model / no setup.
  ml_score: string | null;
}

export const PATTERN_LABELS: Record<string, string> = {
  vcp: "VCP",
  cwh: "CWH",
  flat_base: "Flat Base",
  high_tight_flag: "HTF",
  ascending_triangle: "Asc Tri",
  three_weeks_tight: "3WT",
  bull_flag: "Bull Flag",
};

export const BUYABILITY_LABELS: Record<string, string> = {
  at_pivot: "At pivot",
  in_base: "In base",
  extended: "Extended",
  broken: "Broken",
  frozen: "Frozen",
  no_pattern: "No setup",
};

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
