import type { LivePosition, LivePreview, LotGate, LotPath, LotSignal, OpenLot } from "../api/types";

export function fmtWhen(iso?: string | null): string {
  if (!iso) return "never recorded";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const sec = Math.round((Date.now() - d.getTime()) / 1000);
  const abs = Math.abs(sec);
  const rel =
    abs < 60
      ? `${abs}s`
      : abs < 3600
        ? `${Math.round(abs / 60)}m`
        : abs < 86400
          ? `${Math.round(abs / 3600)}h`
          : `${Math.round(abs / 86400)}d`;
  const when = `${d.toISOString().replace("T", " ").slice(0, 16)} UTC`;
  if (sec >= 0) return `${rel} ago (${when})`;
  return `in ${rel} (${when})`;
}

/** Short local date for when a name joined the champion pool. Never invent a date. */
export function fmtChampionSince(iso?: string | null): string {
  if (!iso) return "before dating";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function certaintyLabel(c?: string | null): string {
  switch (c) {
    case "stamp":
      return "stamp";
    case "inferred":
      return "inferred";
    case "no_signal":
      return "no signal";
    default:
      return "last-known";
  }
}

export function positionEntry(p: LivePosition): number | undefined {
  return p.entry_price ?? p.entry;
}

export function positionStop(p: LivePosition): number | undefined {
  return p.stop_loss ?? p.stop;
}

export function positionPnl(p: LivePosition): number | undefined {
  return p.pnl ?? p.unrealized_pnl;
}

export function positionPnlPct(p: LivePosition): number | undefined {
  return p.pnl_pct ?? p.unrealized_pct;
}

export function accountLabel(raw?: string): string {
  if (!raw) return "—";
  return raw.startsWith("trades_") ? raw.slice("trades_".length) : raw;
}

/** Same slug as hedge_fund.trading.open_lots.account_slug. */
export function accountSlug(name: string): string {
  return name.replace(/[/:]/g, "_");
}

/**
 * Keys that identify one paper account: champion name, open_lots.py slug
 * (/ and : → _), and the trades_* sqlite stem /api/live stamps on rows.
 */
export function paperAccountKeys(name: string): string[] {
  const stripped = name.startsWith("trades_") ? name.slice("trades_".length) : name;
  const slug = accountSlug(name);
  const strippedSlug = accountSlug(stripped);
  return [...new Set([name, slug, stripped, strippedSlug, `trades_${slug}`, `trades_${strippedSlug}`])];
}

export function accountsMatch(account: string | undefined, championName: string): boolean {
  if (!account) return false;
  const champ = new Set(paperAccountKeys(championName));
  return paperAccountKeys(account).some((k) => champ.has(k));
}

export function lotsForChampion(
  positions: LivePosition[] | undefined,
  championName: string,
): LivePosition[] {
  if (!positions?.length) return [];
  return positions.filter((p) => accountsMatch(p.account, championName));
}

export type PairLotSplit = { btc: number; eth: number; total: number };

/** BTC vs ETH from a /api/live symbol. Unit is lots, not symbol-rows. */
export function pairOf(symbol: string | undefined): "BTC" | "ETH" | null {
  if (!symbol) return null;
  const s = symbol.toUpperCase();
  if (s.startsWith("BTC")) return "BTC";
  if (s.startsWith("ETH")) return "ETH";
  return null;
}

function addPairLots(split: PairLotSplit, symbol: string | undefined, n: number): void {
  const pair = pairOf(symbol);
  if (pair === "BTC") split.btc += n;
  else if (pair === "ETH") split.eth += n;
  split.total = split.btc + split.eth;
}

function lotCount(p: LivePosition): number {
  if (p.lots?.length) return p.lots.length;
  return p.lot_count ?? 1;
}

/**
 * Open-lot split for one champion: BTC vs ETH.
 * Prefers /api/live lots[] (one entry per lot). Falls back to that
 * account's position rows using lot_count, never 1-per-symbol-row.
 * Join is the same name/slug/trades_* keys as lotsForChampion.
 */
export function openLotsByPair(
  live: { lots?: OpenLot[]; positions?: LivePosition[] } | undefined | null,
  championName: string,
): PairLotSplit {
  const split: PairLotSplit = { btc: 0, eth: 0, total: 0 };
  if (!live || !championName) return split;

  const lots = (live.lots ?? []).filter((l) => accountsMatch(l.account, championName));
  if (lots.length) {
    for (const l of lots) addPairLots(split, l.symbol, 1);
    return split;
  }

  for (const p of lotsForChampion(live.positions, championName)) {
    addPairLots(split, p.symbol, lotCount(p));
  }
  return split;
}

/** Flatten /api/live lots[] (one row per pyramid lot). Fallback: nested position lots. */
export function flatOpenLots(live?: LivePreview | null): OpenLot[] {
  if (!live) return [];
  if (live.lots?.length) return live.lots;
  const out: OpenLot[] = [];
  for (const p of live.positions ?? []) {
    for (const lot of p.lots ?? []) {
      out.push({
        ...lot,
        account: lot.account ?? p.account,
        current: lot.current ?? p.current,
      });
    }
  }
  return out;
}

/** Open lots for one champion. Unit is lots, not aggregated symbol-rows. */
export function openLotsForChampion(
  live: LivePreview | undefined | null,
  championName: string,
): OpenLot[] {
  if (!live || !championName) return [];
  return flatOpenLots(live).filter((l) => accountsMatch(l.account, championName));
}

export function signalLabel(signal?: LotSignal | string | null): string {
  switch (signal) {
    case "on":
      return "signal on";
    case "would_exit":
      return "would exit";
    case "unknown":
      return "signal ?";
    default:
      return "signal ?";
  }
}

export function pathLabel(path?: LotPath | string | null): string {
  switch (path) {
    case "near_stop":
      return "near stop";
    case "near_tp":
      return "near TP";
    case "mid":
      return "mid";
    default:
      return "—";
  }
}

export function signalTone(signal?: LotSignal | string | null): "pos" | "neg" | "neutral" {
  if (signal === "on") return "pos";
  if (signal === "would_exit") return "neg";
  return "neutral";
}

export function pathTone(path?: LotPath | string | null): "pos" | "warn" | "neutral" {
  if (path === "near_tp") return "pos";
  if (path === "near_stop") return "warn";
  return "neutral";
}

export function lotUnrealized(lot: OpenLot): number | undefined {
  if (lot.unrealized_pnl != null) return lot.unrealized_pnl;
  if (lot.current == null) return undefined;
  return (lot.current - lot.entry) * lot.quantity;
}

export function formatGate(gate?: LotGate | null): string | null {
  if (!gate) return null;
  const retPct = `${(gate.ret * 100).toFixed(1)}%`;
  const thrPct = `${(gate.threshold * 100).toFixed(0)}%`;
  return `${gate.lookback}-bar ${retPct} vs ${thrPct}`;
}

/** Collapsed-card counts: only the non-default flags, so mid/on do not clutter. */
export function lotHealthSummary(lots: OpenLot[]): { wouldExit: number; nearStop: number; nearTp: number } {
  let wouldExit = 0;
  let nearStop = 0;
  let nearTp = 0;
  for (const lot of lots) {
    if (lot.signal === "would_exit") wouldExit += 1;
    if (lot.path === "near_stop") nearStop += 1;
    if (lot.path === "near_tp") nearTp += 1;
  }
  return { wouldExit, nearStop, nearTp };
}

/** Shared UI count: open lots, never champion/account rows. */
export function openLotsTotal(
  live?: { open_lots?: number; positions?: LivePosition[] } | null,
  statusOpen?: number | null,
): number {
  if (live?.open_lots != null) return live.open_lots;
  if (live?.positions?.length) {
    return live.positions.reduce((n, p) => n + (p.lot_count ?? 1), 0);
  }
  return statusOpen ?? 0;
}

export function cadenceLabel(seconds: number): string {
  if (seconds % 3600 === 0) {
    const h = seconds / 3600;
    return h === 1 ? "every 1 hour" : `every ${h} hours`;
  }
  if (seconds % 60 === 0) return `every ${seconds / 60} minutes`;
  return `every ${seconds}s`;
}
