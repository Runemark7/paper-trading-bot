/** Number pyramid lots as separate trades: 1 open/1 close, 2 open/2 close, … */

import type { CandleBar, Champion, ChartSymbol, LivePreview, OpenLot, TradeRow } from "../api/types";
import { accountsMatch } from "../status/format";

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

/** Active paper-book name with the most open lots, else the first row. */
export function defaultChampionName(champs: Champion[]): string | null {
  if (!champs.length) return null;
  return champs.reduce((best, c) => ((c.open_lots ?? 0) > (best.open_lots ?? 0) ? c : best)).name;
}

export function lotsFromLive(live: LivePreview | undefined, symbol: ChartSymbol | string): OpenLot[] {
  if (!live) return [];
  const fromFlat = (live.lots ?? []).filter((l) => l.symbol === symbol);
  if (fromFlat.length) return fromFlat;
  const out: OpenLot[] = [];
  for (const p of live.positions ?? []) {
    if (p.symbol !== symbol) continue;
    if (p.lots?.length) {
      out.push(...p.lots.map((l) => ({ ...l, account: l.account ?? p.account })));
    }
  }
  return out;
}

/** Open lots for one champion + symbol. Join uses the same keys as open_lots.py. */
export function lotsForChampionSymbol(
  live: LivePreview | undefined,
  championName: string,
  symbol: ChartSymbol | string,
): OpenLot[] {
  if (!championName) return [];
  return lotsFromLive(live, symbol).filter((l) => accountsMatch(l.account, championName));
}

export function closedTradesForChampionSymbol(
  trades: TradeRow[] | undefined,
  championName: string,
  symbol: ChartSymbol | string,
): TradeRow[] {
  if (!championName) return [];
  return (trades ?? []).filter(
    (t) => t.symbol === symbol && Boolean(t.exit_ts) && accountsMatch(t.account, championName),
  );
}

/** Keep closed overlays on the loaded 5m window so old history does not bury the tape. */
export function tradesInCandleWindow(trades: TradeRow[], candles: CandleBar[]): TradeRow[] {
  if (!candles.length) return [];
  const t0 = candles[0].t;
  const t1 = candles[candles.length - 1].t;
  return trades.filter((tr) => {
    for (const iso of [tr.entry_ts, tr.exit_ts]) {
      if (!iso) continue;
      const ms = Date.parse(iso);
      if (Number.isFinite(ms) && ms >= t0 && ms <= t1) return true;
    }
    return false;
  });
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
