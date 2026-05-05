export interface Position {
  id: string;
  account_id: string;
  symbol: string;
  currency: string;
  quantity: string;
  avg_cost: string;
  current_price: string | null;
  market_value: string;
  open_pnl: string;
  is_cash_equivalent: boolean;
  is_managed: boolean;
  is_buy_and_hold: boolean;
  ticket_id: string | null;
  as_of: string;
}

export interface BuyingPower {
  currency: string;
  cash: string;
  cash_equivalents: string;
  freeable_total: string;
}

export interface PositionsData {
  positions: Position[];
  buying_power: BuyingPower[];
}
