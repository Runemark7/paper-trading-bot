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

export function cadenceLabel(seconds: number): string {
  if (seconds % 3600 === 0) {
    const h = seconds / 3600;
    return h === 1 ? "every 1 hour" : `every ${h} hours`;
  }
  if (seconds % 60 === 0) return `every ${seconds / 60} minutes`;
  return `every ${seconds}s`;
}
