import type { DiscoveryEvaluation } from "../api/types";

export type ResultFilter = "all" | "qualified" | "rejected";

export type TestedColumnFilters = {
  strategy: string;
  result: ResultFilter;
  testedAt: string;
  sharpe: string;
  trades: string;
  testPnl: string;
  failReasons: string;
};

export const EMPTY_TESTED_FILTERS: TestedColumnFilters = {
  strategy: "",
  result: "all",
  testedAt: "",
  sharpe: "",
  trades: "",
  testPnl: "",
  failReasons: "",
};

const NUM_RE = /^\s*(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)\s*$/;

export function testedFiltersActive(filters: TestedColumnFilters): boolean {
  return (
    filters.result !== "all" ||
    Boolean(
      filters.strategy.trim() ||
        filters.testedAt.trim() ||
        filters.sharpe.trim() ||
        filters.trades.trim() ||
        filters.testPnl.trim() ||
        filters.failReasons.trim(),
    )
  );
}

export function matchText(haystack: string | null | undefined, needle: string): boolean {
  const q = needle.trim().toLowerCase();
  if (!q) return true;
  return (haystack ?? "").toLowerCase().includes(q);
}

export function matchNumeric(
  value: number | null | undefined,
  raw: string,
  display?: string,
): boolean {
  const q = raw.trim();
  if (!q) return true;
  const m = NUM_RE.exec(q);
  if (m?.[1]) {
    if (value == null || Number.isNaN(value)) return false;
    const n = Number(m[2]);
    switch (m[1]) {
      case ">":
        return value > n;
      case ">=":
        return value >= n;
      case "<":
        return value < n;
      case "<=":
        return value <= n;
      default:
        return value === n;
    }
  }
  const shown = display ?? (value == null || Number.isNaN(value) ? "—" : String(value));
  return shown.toLowerCase().includes(q.toLowerCase());
}

export function filterTestedRows(
  rows: DiscoveryEvaluation[],
  filters: TestedColumnFilters,
  formatWhen: (iso?: string | null) => string,
  formatPnl: (n: number | null | undefined) => string,
): DiscoveryEvaluation[] {
  return rows.filter((d) => {
    if (filters.result === "qualified" && !d.qualified) return false;
    if (filters.result === "rejected" && d.qualified) return false;
    if (!matchText(d.strategy, filters.strategy)) return false;
    const whenHaystack = `${d.tested_at} ${formatWhen(d.tested_at)}`;
    if (!matchText(whenHaystack, filters.testedAt)) return false;
    const sharpeDisp = d.sharpe != null ? d.sharpe.toFixed(2) : "—";
    if (!matchNumeric(d.sharpe, filters.sharpe, sharpeDisp)) return false;
    const tradesDisp = d.trades != null ? String(d.trades) : "—";
    if (!matchNumeric(d.trades, filters.trades, tradesDisp)) return false;
    if (!matchNumeric(d.test_pnl, filters.testPnl, formatPnl(d.test_pnl))) return false;
    const fails = (d.fail_reasons ?? []).join("; ");
    if (!matchText(fails, filters.failReasons)) return false;
    return true;
  });
}
