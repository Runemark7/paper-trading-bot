/**
 * Gate v7 checks in plain words. Thresholds mirror hedge_fund/trading/constants.py
 * (GATE_RULES gate_v7_library_profit_20261029). These are the daily library
 * rules. The 5m path still vetoes on Sharpe, sma_stack, DSR, and PBO.
 * Display only; the server decides.
 */

export type GateKey =
  | "trades"
  | "profit"
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

/** Hard vetoes for the daily library. A miss here rejects the name. */
export const GATES: GateInfo[] = [
  {
    key: "trades",
    label: "Enough trades",
    rule: "≥ 8 OOS round-trips and ≥ 2 holdout trades",
    plain: "At least 8 closed round-trips on the warmed daily book and 2 in the holdout year. The 5m path still requires 30 and 8.",
    stage: "floor",
  },
  {
    key: "profit",
    label: "Profitable after fees",
    rule: "OOS and holdout net P&L > 0",
    plain: "Ending equity above the $10,000 start, after the 0.1% taker fee and 2 bps slippage, on the test years (2018-12-01 through 2025-09-30) and again on the sealed holdout year.",
    stage: "floor",
  },
  {
    key: "holdout",
    label: "Sealed holdout year",
    rule: "2025-10-01 → 2026-10-01, P&L > 0",
    plain: "A full year the search never sees. The daily library must still finish that year in profit, with at least 2 trades. Holdout Sharpe is a warning.",
    stage: "robustness",
  },
  {
    key: "perturb",
    label: "Neighbours stay profitable",
    rule: "≥ 70% pass the profit checks",
    plain: "At least 70% of the declared parameter neighbours must also have positive net P&L out of sample, in the holdout, and on both spans at the higher costs.",
    stage: "robustness",
  },
  {
    key: "cost",
    label: "Survives higher costs",
    rule: "P&L > 0 at 2× fees + 5 bps",
    plain: "Re-run with double taker fees and 5 bps extra slippage. Net P&L must stay positive on the test years and on the holdout.",
    stage: "robustness",
  },
];

/** Computed and shown. A miss here does not reject a library name. */
export const WARNING_GATES: GateInfo[] = [
  {
    key: "sharpe",
    label: "Sharpe floor",
    rule: "warning if Sharpe < 0.30",
    plain: "Daily-equity Sharpe below 0.30 on the test years or the holdout is recorded. It does not reject a library name. The 5m path still vetoes on it.",
    stage: "other",
  },
  {
    key: "baseline",
    label: "Versus sma_stack",
    rule: "warning if P&L is not above sma_stack",
    plain: "Losing to the plain moving-average stack, out of sample or in the holdout, is a warning. The 5m path still requires beating it.",
    stage: "other",
  },
  {
    key: "dsr",
    label: "Deflated Sharpe",
    rule: "warning if DSR < 0.95",
    plain: "DSR corrects Sharpe for the 64 library configurations. Below 0.95 is stored with the trial count and shown. It is not a veto.",
    stage: "other",
  },
  {
    key: "pbo",
    label: "Overfitting probability",
    rule: "warning if PBO > 0.20",
    plain: "PBO across this strategy and its neighbours is stored. Above 0.20 is a warning, not a rejection.",
    stage: "other",
  },
  {
    key: "bh_legacy",
    label: "Buy-and-hold",
    rule: "warning, never a pass rule",
    plain: "The success criterion is profit after fees, not beating buy-and-hold. A loss to buy-and-hold is shown beside the result.",
    stage: "other",
  },
];

const EXTRA: GateInfo[] = [
  {
    key: "windows",
    label: "Incomplete data",
    rule: "~390-day blocks tile the warmed span",
    plain: "PBO blocks of about 390 days must tile the warmed daily book through the day before the holdout. The 5m path still uses 18 windows of 87 days.",
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

const BY_KEY: Record<string, GateInfo> = Object.fromEntries(
  [...GATES, ...WARNING_GATES, ...EXTRA].map((g) => [g.key, g]),
);

export function gateInfo(key: string): GateInfo {
  return BY_KEY[key] ?? { key: "ops", label: key, rule: "", plain: key, stage: "other" };
}

/** Same mapping as hedge_fund/web/discovery_insights.classify_reason. */
export function classifyReason(reason: string): GateKey {
  const r = reason.trim().toLowerCase();
  if (r.startsWith("holdout")) return "holdout";
  if (r.startsWith("oos_trades")) return "trades";
  if (r.startsWith("oos_pnl") && !r.includes("sma_stack") && !r.includes("buy-and-hold")) return "profit";
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
  trades: 8,
  holdoutTrades: 2,
};

export const HOLDOUT_SPAN = "2025-10-01 → 2026-10-01";
