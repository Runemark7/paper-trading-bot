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

/** Pre-registered forward-test kill criteria. Flag only; never an auto-retire. */
export interface ForwardStatus {
  status: "not_registered" | "pending" | "ok" | "flagged" | "unavailable" | string;
  action?: string;
  flagged?: boolean;
  reasons?: string[];
  eligible?: boolean;
  days_live?: number | null;
  closed_trades?: number;
  live_sharpe?: number | null;
  live_max_dd?: number | null;
  backtest_max_dd?: number | null;
  registered?: boolean;
}

export interface Champion {
  name: string;
  closed: number;
  pnl: number;
  wins: number;
  passed?: boolean;
  open_lots?: number;
  /** UTC ISO-8601 when this name joined the paper champion pool, or null if unknown. */
  champion_since?: string | null;
  source?: string;
  /** Read-only status from the criteria stored at admission. */
  forward_status?: ForwardStatus | null;
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
  /** Informational. Graduation does not compare against this. */
  buy_and_hold_pnl?: number | null;
  buy_and_hold_role?: "informational" | string;
  trade_history: GraduatedTrade[];
}

export interface DiscoveryEvaluation {
  strategy: string;
  tested_at: string;
  train_pnl?: number;
  test_pnl: number;
  sharpe: number;
  win_rate_pct?: number;
  trades: number;
  qualified: boolean;
  score?: number;
  fail_reasons?: string[];
  timeframe?: string;
  risk_policy?: string;
  all_windows_nonneg?: boolean | null;
  bh_oos_pnl?: number | null;
  sma_stack_oos_pnl?: number | null;
  daily_sharpe?: number | null;
  bh_daily_sharpe?: number | null;
  /** Buy-and-hold figures are reported and are not a pass requirement. */
  bh_role?: "informational" | string;
}

export interface DiscoveryWorker {
  worker_id: string;
  last_seen?: string | null;
  last_seen_age_seconds?: number | null;
  status?: string | null;
  seen?: boolean;
  in_flight: string[];
  lease_count: number;
}

export interface DiscoveryLeases {
  active: number;
  expired: number;
}

export interface DiscoveryInFlight {
  active: boolean;
  running: boolean;
  stale?: boolean;
  source?: string | null;
  names: string[];
  current?: string | null;
  remaining?: string[];
  completed?: string[];
  batch_size: number | null;
  started_at: string | null;
  stamp_started_at?: string | null;
  note: string;
}

export interface DiscoveryCounts {
  universe?: number;
  tested_pass: number;
  tested_fail: number;
  tested: number;
  /** Uncapped unique count from the results index header when published. */
  unique_tested?: number;
  /** Capped display-log length. Totals do not use this. */
  log_rows?: number;
  untested: number;
  leftovers?: number;
  rejected_parked?: number;
  eligible?: number;
  extended?: number;
  champions: number;
  graduated: number;
  in_flight: number;
  leased?: number;
  workers?: number;
  evals_today?: number;
  /** Uncapped tested-name index (dedupe). Pass/fail totals use the results header. */
  tested_index?: number;
}

export interface DiscoveryRefill {
  source: "recipe" | "densify" | "random" | "paired" | "mixed" | string;
  eligible: number;
  generated_last: number;
  exhausted: boolean;
  strategy?: string;
  random?: number;
  paired?: number;
  exploit?: number;
}

/** One family or parameter-bucket row from GET /api/discovery/blocks. */
export interface BlockEffect {
  block: string;
  level: "family" | "bucket" | string;
  n_with: number;
  n_without: number;
  mean_daily_sharpe_with: number | null;
  mean_daily_sharpe_without: number | null;
  mean_trade_sharpe_with: number | null;
  mean_trade_sharpe_without: number | null;
  mean_trades_with: number | null;
  mean_trades_without: number | null;
  pass_rate_with: number | null;
  pass_rate_without: number | null;
  depth_controlled_daily_sharpe: number | null;
  method: string;
}

/** Child minus parent on one swap axis. Zeros are counted and left out of the sign test. */
export interface PairedAxisEffect {
  axis: string;
  n: number;
  n_positive: number;
  n_negative: number;
  n_zero: number;
  mean_daily_sharpe_delta: number | null;
  ci95_low: number | null;
  ci95_high: number | null;
  sign_test_p: number | null;
}

/** Exploratory marginal effects. Does not change the gate. */
export interface BlockReport {
  /** "stale" while a newer report is being built; the numbers are still the last good ones. */
  status?: "ready" | "stale" | string;
  age_seconds?: number | null;
  source?: "cycle" | "web" | string;
  paper_only: boolean;
  exploratory: boolean;
  changes_gate: boolean;
  label: string;
  gate_rules: string;
  n_results: number;
  method: string;
  families: BlockEffect[];
  buckets: BlockEffect[];
  paired: PairedAxisEffect[];
}

export type DiscoveryFarmStatus = "running" | "paused" | "worker_idle" | "worker_unseen" | string;

export interface DiscoveryFarm {
  enabled: boolean;
  status: DiscoveryFarmStatus;
  worker_seen?: boolean;
  updated_at?: string | null;
  heartbeat_at?: string | null;
  heartbeat_age_seconds?: number | null;
  heartbeat_status?: string | null;
  source?: string;
  note?: string;
  paper_only?: boolean;
}

export interface DiscoverySummary {
  paper_only: boolean;
  as_of: string;
  /** True when GET ?compact=1 omitted tested / queued / extended_names lists. */
  compact?: boolean;
  certainty: string;
  running: boolean;
  stamp_says_in_progress: boolean;
  stale?: boolean;
  stuck?: boolean;
  stuck_reason?: string | null;
  tested: DiscoveryEvaluation[];
  discovery_on_cycle?: boolean;
  discovery_farm?: "windows_worker" | "cycle_sidecar" | string;
  farm?: DiscoveryFarm;
  /** Per-worker heartbeats and the names each one currently has leased. */
  workers?: DiscoveryWorker[];
  leases?: DiscoveryLeases;
  extended_names?: string[];
  in_flight: DiscoveryInFlight;
  queued: string[];
  untested: string[];
  counts: DiscoveryCounts;
  /** Last claim refill. ``eligible`` matches ``counts.eligible``. */
  refill?: DiscoveryRefill;
  last_tested_at: string | null;
  last_strategy: string | null;
  last_eval_age_seconds?: number | null;
  note: string;
  gate?: {
    rules: string;
    criteria: string;
    bh_role: string;
  };
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
      unique_tested?: number;
      last_tested_at: string | null;
      last_strategy: string | null;
      last_qualified: boolean | null;
      stuck?: boolean;
      file: { path: string; mtime: string | null; exists: boolean };
    };
    tournament: CertaintyNote & {
      stamp_says_this_phase?: boolean;
      active_count: number;
      evaluation_limit: number;
      synced_until: string | null;
      file: { path: string; mtime: string | null; exists: boolean };
    };
    replenish: CertaintyNote & {
      needed: boolean;
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

export interface ForwardCandidate {
  rank: number;
  strategy: string;
  dsr: number | null;
  daily_sharpe: number | null;
  pbo: number | null;
  sharpe?: number | null;
  bh_daily_sharpe?: number | null;
  bh_oos_pnl?: number | null;
  bh_role?: string;
  proposed: boolean;
  admitted: boolean;
  status: string;
}

export interface ForwardCandidates {
  paper_only: boolean;
  cap: number;
  active: number;
  open_slots: number;
  eligible: number;
  proposed: ForwardCandidate[];
  admitted: string[];
  retired: string[];
  note: string;
}

export interface ChampionsPayload {
  active_champions: Champion[];
  active_count: number;
  evaluation_limit: number;
  forward_champion_cap?: number;
  forward_candidates?: ForwardCandidates;
  graduated_count: number;
  graduated: GraduatedStrategy[];
  synced_until?: string | null;
  open_lots?: number;
  open_lots_by_account?: Record<string, number>;
  open_lots_unit?: "open_lots" | string;
}

/** One entry or exit from GET /api/champions/trades. P&L is net of both taker fees. */
export interface TradeEvent {
  kind: "entry" | "exit";
  ts: string | null;
  champion: string;
  trade_id: number | null;
  lot_id: number | null;
  symbol: string | null;
  size: number | null;
  entry_price: number | null;
  price: number | null;
  notional: number | null;
  fee: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  reason: string | null;
  open: boolean;
}

export interface ChampionTradeSummary {
  name: string;
  active: boolean;
  has_account: boolean;
  start_cash: number;
  truncated: boolean;
  trades_total: number;
  closed: number;
  open: number;
  wins: number;
  realized_pnl: number;
  fees_paid: number;
  equity: number | null;
  equity_at: string | null;
  open_entries: TradeEvent[];
}

export interface ChampionTradesPayload {
  paper_only: boolean;
  as_of: string;
  name: string | null;
  found: boolean;
  limit: number;
  total_events: number;
  events: TradeEvent[];
  champions: ChampionTradeSummary[];
  pnl_note: string;
}

export interface InsightGateNumbers {
  dsr: number | null;
  pbo: number | null;
  neighbours: number | null;
  neighbours_pass: number | null;
  cost_sharpe: number | null;
  holdout_sharpe: number | null;
  holdout_trades: number | null;
}

export interface InsightRow {
  strategy: string | null;
  tested_at: string | null;
  qualified: boolean;
  sharpe: number | null;
  daily_sharpe: number | null;
  trades: number | null;
  test_pnl: number | null;
  failed_gates: string[];
  fail_reasons: string[];
  gate: InsightGateNumbers | null;
  gate_rules: string | null;
}

/** GET /api/discovery/insights — display-log scope, newest evaluation per name. */
export interface DiscoveryInsights {
  paper_only: boolean;
  as_of: string;
  gate_rules: string;
  scope: string;
  rows_scanned: number;
  current_gate: { tested: number; cleared_base_floors: number; passed: number };
  gate_failures: { gate: string; count: number }[];
  near_misses: InsightRow[];
  recent: InsightRow[];
  note: string;
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
