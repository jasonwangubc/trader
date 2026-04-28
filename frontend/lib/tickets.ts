export interface Account {
  id: string;
  questrade_account_id: string;
  type: string;
  primary_currency: string;
  nickname: string | null;
  real_money_enabled: boolean;
  balances: { currency: string; total_equity: string; cash: string; market_value: string; buying_power: string }[];
}

export interface HouseholdData {
  accounts: Account[];
  household_equity: Record<string, string>;
}

export interface SizingPreview {
  risk_pct: string;
  base_risk_pct: string;
  multiplier: string;
  capped: boolean;
  equity_basis: string;
  equity_currency: string;
  risk_amount: string;
  per_share_risk: string;
  shares: number;
  position_value: string;
  warnings: string[];
}

export interface StreakSnapshot {
  consecutive_wins: number;
  consecutive_losses: number;
  multiplier: string;
  cooldown_active: boolean;
  last_outcome: string | null;
}

export interface TicketPreviewOut {
  sizing: SizingPreview;
  streak: StreakSnapshot;
}

export interface Ticket {
  id: string;
  account_id: string;
  symbol: string;
  currency: string;
  setup_type: string;
  trigger_type: string;
  trigger_price: string;
  stop_price: string;
  target_price: string | null;
  time_stop_days: number | null;
  risk_pct: string;
  risk_amount: string;
  streak_multiplier_at_creation: string;
  position_size_shares: number;
  position_size_value: string;
  status: string;
  is_paper: boolean;
  thesis: string | null;
  created_at: string;
  armed_at: string | null;
  expires_at: string | null;
}

export const SETUP_TYPES = [
  { value: "VCP", label: "VCP (Volatility Contraction)" },
  { value: "flat_base", label: "Flat base" },
  { value: "ep", label: "EP / earnings pivot" },
  { value: "cup_handle", label: "Cup with handle" },
  { value: "pivot", label: "Pivot point" },
  { value: "manual", label: "Manual" },
];

export const TRIGGER_TYPES = [
  { value: "price_above", label: "Price above level" },
  { value: "price_above_with_volume", label: "Price above with volume confirm" },
  { value: "day_close_above", label: "Day close above level" },
];

export function fmtMoney(amount: string | number, currency: string): string {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(typeof amount === "string" ? parseFloat(amount) : amount);
}

export function fmtPct(value: string | number, fractionDigits = 2): string {
  const v = typeof value === "string" ? parseFloat(value) : value;
  return `${(v * 100).toFixed(fractionDigits)}%`;
}
