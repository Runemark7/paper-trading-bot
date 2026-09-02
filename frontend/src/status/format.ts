import type { LivePosition } from "../api/types";

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
