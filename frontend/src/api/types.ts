// API contract — mirrors hedge_fund/web/server.py endpoints.

export interface LotGate {
  kind: "dip" | "mom" | string;
  lookback: number;
  ret: number;
  threshold: number;
}

export type LotSignal = "on" | "would_exit" | "unknown";
export type LotPath = "near_stop" | "mid" | "near_tp";

export interface OpenLot {
  lot_id: number;
  symbol: string;
  entry: number;
  stop: number;
  take_profit: number;
  quantity: number;
  condition?: string;
  entry_ts?: string | null;
  account?: string;
  signal?: LotSignal | string;
  path?: LotPath | string;
  path_pct?: number;
  current?: number | null;
  unrealized_pnl?: number | null;
  unrealized_pct?: number | null;
  gate?: LotGate | null;
}

export interface LivePosition {
  symbol: string;
  entry_price?: number;
  entry?: number;
  entry_time?: string;
  current?: number;
  stop_loss?: number;
  stop?: number;
  quantity: number;
  condition?: string;
  pnl?: number;
  pnl_pct?: number;
  unrealized_pnl?: number;
  unrealized_pct?: number;
  account?: string;
  lot_count?: number;
  lots?: OpenLot[];
}

export interface LivePreview {
  live_equity: number;
  cash: number;
  positions: LivePosition[];
  lots?: OpenLot[];
  as_of?: string;
  accounts?: number;
  open_lots?: number;
  open_lots_by_account?: Record<string, number>;
  open_lots_unit?: "open_lots" | string;
}

export interface RegimeState {
  zone: string;
  score: number;
  allowed: boolean;
  components?: Record<string, unknown>;
  error?: string;
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
  account?: string;
  lot_id?: number | null;
}

export interface Summary {
  equity?: number | null;
  live_equity?: number;
  closed_trades: number;
  win_rate: number | null;
  total_pnl: number | null;
  brier?: number;
  calibrated_samples?: number;
  updated?: string | null;
  source?: string;
  account_count?: number;
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
  open_lots?: number;
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
  status: "GRADUATED_PAPER" | "REJECTED_NEGATIVE_PNL";
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

export interface CertaintyNote {
  certainty?: "stamp" | "last_known" | "inferred" | "no_signal";
  running?: boolean;
  note?: string;
}

export interface StatusSnapshot {
  paper_only: boolean;
  as_of: string;
  running_now: {
    label: string;
    certainty: string;
    strategy: {
      mode: "champion_accounts" | "sma_stack_fallback" | string;
      active: string[];
      fallback: string;
      source: string;
    };
    accounts: {
      count: number;
      kind: string;
      names: string[];
    };
    cycle: {
      interval_seconds: number;
      bar_timeframe: string;
      window: string;
      last_cycle_at: string | null;
      account_saved_at: string | null;
      next: {
        at: string | null;
        inferred: boolean;
        overdue?: boolean;
        in_window_now: boolean;
        note: string;
      };
    };
    positions_open: number;
    open_lots?: number;
    open_lots_by_account?: Record<string, number>;
    open_lots_unit?: "open_lots" | string;
    heartbeat: {
      configured_interval_seconds: number;
      role: string;
      last_pass_at: string | null;
      last_closed?: number;
      accounts_checked?: number;
      recent: boolean;
      certainty: string;
      on: boolean | null;
      note: string;
    };
    regime: {
      gates_live_book: boolean;
      display_only: boolean;
      note: string;
    };
  };
  in_progress: {
    job_runner: boolean;
    note: string;
    pipeline: {
      job_runner: boolean;
      phase: string | null;
      status: string | null;
      certainty: string;
      running: boolean;
      stamp_says_in_progress?: boolean;
      stale: boolean;
      started_at: string | null;
      finished_at: string | null;
      at: string | null;
      source?: string;
      note: string;
    };
    discovery: CertaintyNote & {
      log_count: number;
      last_tested_at: string | null;
      last_strategy: string | null;
      last_qualified: boolean | null;
      file: { path: string; mtime: string | null; exists: boolean };
    };
    tournament: CertaintyNote & {
      stamp_says_this_phase?: boolean;
      active_count: number;
      target_active: number;
      slots_open: number;
      evaluation_limit: number;
      synced_until: string | null;
      file: { path: string; mtime: string | null; exists: boolean };
    };
    replenish: CertaintyNote & {
      needed: boolean;
      slots_open: number;
      stamp_says_this_phase?: boolean;
    };
    graduation: CertaintyNote & {
      count: number;
      last_name: string | null;
      last_status: string | null;
      last_graduated_at: string | null;
      token: string;
    };
    isolated_cycle: CertaintyNote & {
      stamp_says_this_phase?: boolean;
      last_cycle_at: string | null;
    };
  };
}

export interface ChampionsPayload {
  active_champions: Champion[];
  active_count: number;
  target_active: number;
  evaluation_limit: number;
  graduated_count: number;
  graduated: GraduatedStrategy[];
  synced_until?: string | null;
  open_lots?: number;
  open_lots_by_account?: Record<string, number>;
  open_lots_unit?: "open_lots" | string;
}

export type ChartSymbol = "BTC/USDT" | "ETH/USDT";

export interface CandleBar {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface CandlesPayload {
  symbol: string;
  timeframe: string;
  candles: CandleBar[];
  as_of?: string;
  source?: string;
  paper_only?: boolean;
}
