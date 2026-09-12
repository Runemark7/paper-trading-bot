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

export type TestedSortKey = "strategy" | "testedAt" | "result" | "sharpe" | "trades" | "testPnl";
export type SortDir = "asc" | "desc";

export type TestedSort = {
  key: TestedSortKey;
  dir: SortDir;
};

/** Matches GET /api/discovery/summary: latest-eval-per-name, newest tested_at first. */
export const DEFAULT_TESTED_SORT: TestedSort = { key: "testedAt", dir: "desc" };

export const TESTED_SORT_COLUMNS: { key: TestedSortKey; label: string }[] = [
  { key: "testedAt", label: "When" },
  { key: "testPnl", label: "Test P&L" },
  { key: "sharpe", label: "Sharpe" },
  { key: "trades", label: "OOS trades" },
  { key: "strategy", label: "Name" },
  { key: "result", label: "Result" },
];

export function testedSortColumnLabel(key: TestedSortKey): string {
  return TESTED_SORT_COLUMNS.find((c) => c.key === key)?.label ?? key;
}

export function initialSortDir(key: TestedSortKey): SortDir {
  return key === "strategy" ? "asc" : "desc";
}

export function sortDirLabels(key: TestedSortKey): { asc: string; desc: string } {
  switch (key) {
    case "strategy":
      return { asc: "A–Z", desc: "Z–A" };
    case "testedAt":
      return { asc: "Oldest", desc: "Newest" };
    case "result":
      return { asc: "Rejected first", desc: "Qualified first" };
    default:
      return { asc: "Lowest", desc: "Highest" };
  }
}

export function sortsEqual(a: TestedSort, b: TestedSort): boolean {
  return a.key === b.key && a.dir === b.dir;
}

export function isDefaultTestedSort(sort: TestedSort): boolean {
  return sortsEqual(sort, DEFAULT_TESTED_SORT);
}

/** Header click: other column starts at preferred dir; same column toggles then returns to default. */
export function cycleTestedSort(current: TestedSort, clicked: TestedSortKey): TestedSort {
  const preferred = initialSortDir(clicked);
  if (current.key !== clicked) {
    return { key: clicked, dir: preferred };
  }
  if (current.dir === preferred) {
    return { key: clicked, dir: preferred === "desc" ? "asc" : "desc" };
  }
  return { ...DEFAULT_TESTED_SORT };
}

export function testedSortSummary(sort: TestedSort): string {
  const labels = sortDirLabels(sort.key);
  if (sort.key === "testedAt") return sort.dir === "desc" ? "newest first" : "oldest first";
  if (sort.key === "strategy") return `name ${labels[sort.dir]}`;
  if (sort.key === "result") return labels[sort.dir].toLowerCase();
  return `${labels[sort.dir].toLowerCase()} ${testedSortColumnLabel(sort.key)}`;
}

type SortScalar = number | string | null;

function sortValue(row: DiscoveryEvaluation, key: TestedSortKey): SortScalar {
  switch (key) {
    case "strategy":
      return row.strategy.toLowerCase();
    case "testedAt": {
      const t = Date.parse(row.tested_at);
      return Number.isNaN(t) ? null : t;
    }
    case "result":
      return row.qualified ? 1 : 0;
    case "sharpe":
      return row.sharpe == null || Number.isNaN(row.sharpe) ? null : row.sharpe;
    case "trades":
      return row.trades == null || Number.isNaN(row.trades) ? null : row.trades;
    case "testPnl":
      return row.test_pnl == null || Number.isNaN(row.test_pnl) ? null : row.test_pnl;
  }
}

function compareSortValues(a: SortScalar, b: SortScalar, dir: SortDir): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  let cmp = 0;
  if (typeof a === "string" && typeof b === "string") {
    cmp = a.localeCompare(b);
  } else {
    const na = Number(a);
    const nb = Number(b);
    cmp = na < nb ? -1 : na > nb ? 1 : 0;
  }
  return dir === "asc" ? cmp : -cmp;
}

export function compareTestedRows(a: DiscoveryEvaluation, b: DiscoveryEvaluation, sort: TestedSort): number {
  const primary = compareSortValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir);
  if (primary !== 0) return primary;
  const when = compareSortValues(sortValue(a, "testedAt"), sortValue(b, "testedAt"), "desc");
  if (when !== 0) return when;
  return compareSortValues(sortValue(a, "strategy"), sortValue(b, "strategy"), "asc");
}

export function sortTestedRows(rows: DiscoveryEvaluation[], sort: TestedSort): DiscoveryEvaluation[] {
  return [...rows].sort((a, b) => compareTestedRows(a, b, sort));
}

/** Filter first, then sort — used by the Already tested list. */
export function applyTestedRows(
  rows: DiscoveryEvaluation[],
  filters: TestedColumnFilters,
  sort: TestedSort,
  formatWhen: (iso?: string | null) => string,
  formatPnl: (n: number | null | undefined) => string,
): DiscoveryEvaluation[] {
  return sortTestedRows(filterTestedRows(rows, filters, formatWhen, formatPnl), sort);
}
