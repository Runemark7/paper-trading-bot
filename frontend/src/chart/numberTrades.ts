/** Number pyramid lots as separate trades: 1 open/1 close, 2 open/2 close, … */

import type { ChartSymbol, LivePreview, OpenLot, TradeRow } from "../api/types";

/** Same 2:1 as TradingLoop / RiskManager.take_profit_price — fallback only. */
export const TAKE_PROFIT_RR = 2;

export function takeProfitPrice(entry: number, stop: number, rr = TAKE_PROFIT_RR): number {
  return entry + rr * (entry - stop);
}

export interface NumberedOpenLot {
  n: number;
  lot: OpenLot;
}

export interface NumberedClosedTrade {
  n: number;
  trade: TradeRow;
}

export function lotsFromLive(live: LivePreview | undefined, symbol: ChartSymbol | string): OpenLot[] {
  if (!live) return [];
  const fromFlat = (live.lots ?? []).filter((l) => l.symbol === symbol);
  if (fromFlat.length) return fromFlat;
  const out: OpenLot[] = [];
  for (const p of live.positions ?? []) {
    if (p.symbol !== symbol) continue;
    if (p.lots?.length) {
      out.push(...p.lots);
    }
  }
  return out;
}

export function numberOpenLots(lots: OpenLot[], symbol: ChartSymbol | string): NumberedOpenLot[] {
  const filtered = lots.filter((l) => l.symbol === symbol);
  const sorted = [...filtered].sort((a, b) => {
    const ta = a.entry_ts || "";
    const tb = b.entry_ts || "";
    if (ta !== tb) return ta.localeCompare(tb);
    const id = (a.lot_id ?? 0) - (b.lot_id ?? 0);
    if (id !== 0) return id;
    return (a.account || "").localeCompare(b.account || "");
  });
  return sorted.map((lot, i) => ({
    n: i + 1,
    lot: {
      ...lot,
      take_profit:
        lot.take_profit != null && Number.isFinite(lot.take_profit)
          ? lot.take_profit
          : takeProfitPrice(lot.entry, lot.stop),
    },
  }));
}

export function numberClosedTrades(trades: TradeRow[] | undefined, symbol: ChartSymbol | string): NumberedClosedTrade[] {
  const filtered = (trades ?? []).filter((t) => t.symbol === symbol && t.exit_ts);
  const sorted = [...filtered].sort((a, b) => {
    const ta = a.entry_ts || "";
    const tb = b.entry_ts || "";
    if (ta !== tb) return ta.localeCompare(tb);
    const lot = (a.lot_id ?? 0) - (b.lot_id ?? 0);
    if (lot !== 0) return lot;
    return (a.id ?? 0) - (b.id ?? 0);
  });
  return sorted.map((trade, i) => ({ n: i + 1, trade }));
}
