// API contract — mirrors hedge_fund/web/server.py endpoints.
export interface LivePosition {
  symbol: string;
  entry_price: number;
  entry_time: string;
  current?: number;
  stop_loss?: number;
  quantity: number;
  condition?: string;
  pnl?: number;
  pnl_pct?: number;
}

export interface LivePreview {
  live_equity: number;
  cash: number;
  positions: LivePosition[];
  as_of?: string;
}

export interface RegimeState {
  zone: string;
  score: number;
  allowed: boolean;
  components?: Record<string, unknown>;
}

export interface TradeRow {
  id: number;
  symbol: string;
  condition: string;
  entry_ts: string;
  entry_price: number;
  exit_ts: string | null;
  exit_price: number | null;
  exit_reason: string | null;
  pnl: number | null;
  pnl_pct: number | null;
  hit: number | null;
}

export interface Summary {
  live_equity: number;
  closed_trades: number;
  win_rate: number;
  total_pnl: number | null;
  brier: number;
  calibrated_samples: number;
}

export interface LearningCondition {
  symbol: string;
  timeframe?: string;
  condition: string;
  trials: number;
  calibrated_prob: number;
  level?: string;
}

// /api/learning returns a dict keyed "symbol|timeframe|condition" -> one row.
export type LearningMap = Record<string, LearningCondition>;

export interface Champion {
  name: string;
  closed: number;
  pnl: number;
  wins: number;
  passed?: boolean;
}

export interface GraduatedTrade {
  symbol: string;
  entry_price: number;
  exit_price: number;
  entry_ts: string;
  exit_ts: string;
  size: number;
  pnl: number;
  pnl_pct: number;
  hit: number;
  exit_reason: string;
}

export interface GraduatedStrategy {
  name: string;
  closed_trades: number;
  total_pnl: number;
  wins: number;
  win_rate_pct: number;
  graduated_at: string;
  status: "READY_FOR_LIVE" | "REJECTED_NEGATIVE_PNL";
  trade_history: GraduatedTrade[];
}

export interface DiscoveryEvaluation {
  strategy: string;
  tested_at: string;
  train_pnl: number;
  test_pnl: number;
  sharpe: number;
  win_rate_pct: number;
  trades: number;
  qualified: boolean;
  score?: number;
}