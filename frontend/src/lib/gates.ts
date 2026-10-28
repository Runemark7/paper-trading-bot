/**
 * Gate v5 checks in plain words. Thresholds mirror hedge_fund/trading/constants.py
 * (GATE_RULES gate_v5_btc_holdout_dsr_pbo_20261027). Display only; the server decides.
 */

export type GateKey =
  | "trades"
  | "sharpe"
  | "baseline"
  | "holdout"
  | "dsr"
  | "pbo"
  | "perturb"
  | "cost"
  | "windows"
  | "bh_legacy"
  | "ops";

export interface GateInfo {
  key: GateKey;
  label: string;
  rule: string;
  plain: string;
  stage: "floor" | "robustness" | "other";
}

export const GATES: GateInfo[] = [
  {
    key: "trades",
    label: "Enough trades",
    rule: "≥ 30 out-of-sample trades",
    plain: "At least 30 trades across the walk-forward test windows, so the result is not a handful of lucky trades.",
    stage: "floor",
  },
  {
    key: "sharpe",
    label: "Steady returns",
    rule: "Sharpe ≥ 0.30",
    plain: "Average out-of-sample Sharpe ratio (return per unit of risk) of at least 0.30.",
    stage: "floor",
  },
  {
    key: "baseline",
    label: "Beats the simple baseline",
    rule: "P&L above sma_stack",
    plain: "Must out-earn a plain moving-average trend filter (sma_stack) on the same windows. If a one-liner does better, the extra rules are not earning their keep.",
    stage: "floor",
  },
  {
    key: "holdout",
    label: "Sealed holdout year",
    rule: "2025-10-01 → 2026-10-01",
    plain: "A full year the search never sees. The strategy must still have Sharpe ≥ 0.30, at least 8 trades, and beat sma_stack there.",
    stage: "robustness",
  },
  {
    key: "dsr",
    label: "Not luck from many tries",
    rule: "Deflated Sharpe ≥ 0.95",
    plain: "Corrects the Sharpe for how many strategies have been tried. With thousands of attempts some look good by chance; 0.95 means about 95% confidence the edge is real.",
    stage: "robustness",
  },
  {
    key: "pbo",
    label: "Low overfitting risk",
    rule: "PBO ≤ 0.20",
    plain: "Probability of backtest overfitting, measured across this strategy and its parameter neighbours. At most a 20% chance the best-looking variant falls apart out of sample.",
    stage: "robustness",
  },
  {
    key: "perturb",
    label: "Survives small tweaks",
    rule: "≥ 70% of neighbours pass",
    plain: "Nudge each parameter by 10–20%. At least 70% of those neighbours must still clear the trade and Sharpe floors, so it is not a lone spike.",
    stage: "robustness",
  },
  {
    key: "cost",
    label: "Survives higher costs",
    rule: "Sharpe ≥ 0.30 at 2× fees + 5 bps",
    plain: "Re-run with double taker fees and 5 bps extra slippage. Sharpe must stay at or above 0.30.",
    stage: "robustness",
  },
];

const EXTRA: GateInfo[] = [
  {
    key: "windows",
    label: "Incomplete data",
    rule: "all 18 windows scored",
    plain: "The backtest did not produce the expected number of walk-forward windows.",
    stage: "other",
  },
  {
    key: "bh_legacy",
    label: "Buy-and-hold (old rule)",
    rule: "info only under v5",
    plain: "An older gate required beating buy-and-hold. Gate v5 reports buy-and-hold for context only.",
    stage: "other",
  },
  {
    key: "ops",
    label: "Operational",
    rule: "timeout / too expensive",
    plain: "Skipped for an operational reason, such as an evaluation timeout or a lookback that is too expensive to test.",
    stage: "other",
  },
];

const BY_KEY: Record<string, GateInfo> = Object.fromEntries([...GATES, ...EXTRA].map((g) => [g.key, g]));

export function gateInfo(key: string): GateInfo {
  return BY_KEY[key] ?? { key: "ops", label: key, rule: "", plain: key, stage: "other" };
}

/** Same mapping as hedge_fund/web/discovery_insights.classify_reason. */
export function classifyReason(reason: string): GateKey {
  const r = reason.trim().toLowerCase();
  if (r.startsWith("holdout")) return "holdout";
  if (r.startsWith("oos_trades")) return "trades";
  if (r.startsWith("oos_sharpe")) return "sharpe";
  if (r.includes("sma_stack")) return "baseline";
  if (r.startsWith("dsr")) return "dsr";
  if (r.startsWith("pbo")) return "pbo";
  if (r.includes("neighbour") || r.includes("neighbor")) return "perturb";
  if (r.startsWith("cost stress")) return "cost";
  if (r.startsWith("windows")) return "windows";
  if (r.includes("b&h") || r.includes("buy-and-hold") || r.includes("buy_and_hold") || r.startsWith("bh")) return "bh_legacy";
  return "ops";
}

export const GATE_THRESHOLDS = {
  dsr: 0.95,
  pbo: 0.2,
  neighbours: 0.7,
  cost: 0.3,
  sharpe: 0.3,
  trades: 30,
  holdoutTrades: 8,
};

export const HOLDOUT_SPAN = "2025-10-01 → 2026-10-01";
export const FORWARD_CAP_DEFAULT = 5;
