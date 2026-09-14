import type { Champion, OpenLot } from "../api/types";

export type SortDir = "asc" | "desc";

const NUM_RE = /^\s*(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)\s*$/;

function matchText(haystack: string | null | undefined, needle: string): boolean {
  const q = needle.trim().toLowerCase();
  if (!q) return true;
  return (haystack ?? "").toLowerCase().includes(q);
}

function matchNumeric(
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

export type ChampionListRow = Champion & {
  btc: number | null;
  eth: number | null;
  openLots: number;
  liveLotsKnown: boolean;
  lots: OpenLot[];
};

export type ChampionColumnFilters = {
  name: string;
  since: string;
  btc: string;
  eth: string;
  openLots: string;
  closed: string;
  wins: string;
  pnl: string;
};

export const EMPTY_CHAMPION_FILTERS: ChampionColumnFilters = {
  name: "",
  since: "",
  btc: "",
  eth: "",
  openLots: "",
  closed: "",
  wins: "",
  pnl: "",
};

export type ChampionSortKey =
  | "name"
  | "since"
  | "btc"
  | "eth"
  | "openLots"
  | "closed"
  | "wins"
  | "pnl";

export type ChampionSort = {
  key: ChampionSortKey;
  dir: SortDir;
};

/** Highest paper P&L first — same tournament ranking feel as Discovery's newest-tested. */
export const DEFAULT_CHAMPION_SORT: ChampionSort = { key: "pnl", dir: "desc" };

export const CHAMPION_SORT_COLUMNS: { key: ChampionSortKey; label: string }[] = [
  { key: "pnl", label: "Paper P&L" },
  { key: "openLots", label: "Open lots" },
  { key: "btc", label: "BTC lots" },
  { key: "eth", label: "ETH lots" },
  { key: "closed", label: "Closed" },
  { key: "wins", label: "Wins" },
  { key: "since", label: "Champion since" },
  { key: "name", label: "Name" },
];

export function championFiltersActive(filters: ChampionColumnFilters): boolean {
  return Boolean(
    filters.name.trim() ||
      filters.since.trim() ||
      filters.btc.trim() ||
      filters.eth.trim() ||
      filters.openLots.trim() ||
      filters.closed.trim() ||
      filters.wins.trim() ||
      filters.pnl.trim(),
  );
}

export function championSortColumnLabel(key: ChampionSortKey): string {
  return CHAMPION_SORT_COLUMNS.find((c) => c.key === key)?.label ?? key;
}

export function initialChampionSortDir(key: ChampionSortKey): SortDir {
  return key === "name" ? "asc" : "desc";
}

export function championSortDirLabels(key: ChampionSortKey): { asc: string; desc: string } {
  switch (key) {
    case "name":
      return { asc: "A–Z", desc: "Z–A" };
    case "since":
      return { asc: "Oldest", desc: "Newest" };
    default:
      return { asc: "Lowest", desc: "Highest" };
  }
}

export function championSortsEqual(a: ChampionSort, b: ChampionSort): boolean {
  return a.key === b.key && a.dir === b.dir;
}

export function isDefaultChampionSort(sort: ChampionSort): boolean {
  return championSortsEqual(sort, DEFAULT_CHAMPION_SORT);
}

export function cycleChampionSort(current: ChampionSort, clicked: ChampionSortKey): ChampionSort {
  const preferred = initialChampionSortDir(clicked);
  if (current.key !== clicked) {
    return { key: clicked, dir: preferred };
  }
  if (current.dir === preferred) {
    return { key: clicked, dir: preferred === "desc" ? "asc" : "desc" };
  }
  return { ...DEFAULT_CHAMPION_SORT };
}

export function championSortSummary(sort: ChampionSort): string {
  const labels = championSortDirLabels(sort.key);
  if (sort.key === "since") return sort.dir === "desc" ? "newest first" : "oldest first";
  if (sort.key === "name") return `name ${labels[sort.dir]}`;
  return `${labels[sort.dir].toLowerCase()} ${championSortColumnLabel(sort.key)}`;
}

export function filterChampionRows(
  rows: ChampionListRow[],
  filters: ChampionColumnFilters,
  formatSince: (iso?: string | null) => string,
  formatPnl: (n: number | null | undefined) => string,
): ChampionListRow[] {
  return rows.filter((row) => {
    if (!matchText(row.name, filters.name)) return false;
    const sinceDisp = formatSince(row.champion_since);
    const sinceHay = `${row.champion_since ?? ""} ${sinceDisp}`;
    if (!matchText(sinceHay, filters.since)) return false;
    const btcDisp = row.btc == null ? "—" : String(row.btc);
    if (!matchNumeric(row.btc, filters.btc, btcDisp)) return false;
    const ethDisp = row.eth == null ? "—" : String(row.eth);
    if (!matchNumeric(row.eth, filters.eth, ethDisp)) return false;
    if (!matchNumeric(row.openLots, filters.openLots, String(row.openLots))) return false;
    if (!matchNumeric(row.closed, filters.closed, String(row.closed))) return false;
    if (!matchNumeric(row.wins, filters.wins, String(row.wins))) return false;
    if (!matchNumeric(row.pnl, filters.pnl, formatPnl(row.pnl))) return false;
    return true;
  });
}

type SortScalar = number | string | null;

function sortValue(row: ChampionListRow, key: ChampionSortKey): SortScalar {
  switch (key) {
    case "name":
      return (row.name ?? "").toLowerCase();
    case "since": {
      if (!row.champion_since) return null;
      const t = Date.parse(row.champion_since);
      return Number.isNaN(t) ? null : t;
    }
    case "btc":
      return row.btc;
    case "eth":
      return row.eth;
    case "openLots":
      return row.openLots;
    case "closed":
      return row.closed;
    case "wins":
      return row.wins;
    case "pnl":
      return row.pnl;
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

export function compareChampionRows(a: ChampionListRow, b: ChampionListRow, sort: ChampionSort): number {
  const primary = compareSortValues(sortValue(a, sort.key), sortValue(b, sort.key), sort.dir);
  if (primary !== 0) return primary;
  const pnl = compareSortValues(sortValue(a, "pnl"), sortValue(b, "pnl"), "desc");
  if (pnl !== 0) return pnl;
  return compareSortValues(sortValue(a, "name"), sortValue(b, "name"), "asc");
}

export function sortChampionRows(rows: ChampionListRow[], sort: ChampionSort): ChampionListRow[] {
  return [...rows].sort((a, b) => compareChampionRows(a, b, sort));
}

export function applyChampionRows(
  rows: ChampionListRow[],
  filters: ChampionColumnFilters,
  sort: ChampionSort,
  formatSince: (iso?: string | null) => string,
  formatPnl: (n: number | null | undefined) => string,
): ChampionListRow[] {
  return sortChampionRows(filterChampionRows(rows, filters, formatSince, formatPnl), sort);
}
