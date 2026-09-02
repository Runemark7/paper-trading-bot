import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, CANDLE_LIMIT } from "../api/client";
import type { ChartSymbol } from "../api/types";
import { Empty, fmt, fmtPct } from "../components/ui";
import PaperChart from "./PaperChart";
import {
  closedTradesForChampionSymbol,
  lotsForChampionSymbol,
  numberClosedTrades,
  numberOpenLots,
  tradesInCandleWindow,
  type NumberedClosedTrade,
  type NumberedOpenLot,
} from "./numberTrades";

const SYMBOLS: ChartSymbol[] = ["BTC/USDT", "ETH/USDT"];
const REFRESH_MS = 20_000;

function SymbolToggle({
  symbol,
  onChange,
}: {
  symbol: ChartSymbol;
  onChange: (s: ChartSymbol) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2" role="group" aria-label="Chart symbol">
      {SYMBOLS.map((s) => {
        const on = s === symbol;
        return (
          <button
            key={s}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(s)}
            className={`min-h-12 rounded-xl text-base font-semibold px-3 transition ${
              on
                ? "bg-indigo-600 text-white ring-2 ring-indigo-300"
                : "bg-white/5 text-white/70 ring-1 ring-white/15"
            }`}
          >
            {s.replace("/USDT", "")}
            <span className="block text-[11px] font-normal opacity-70">/USDT</span>
          </button>
        );
      })}
    </div>
  );
}

function TradeLegend({
  openLots,
  closed,
  compact,
}: {
  openLots: NumberedOpenLot[];
  closed: NumberedClosedTrade[];
  compact: boolean;
}) {
  const closedShown = compact ? closed.slice(-4) : closed.slice(-12);
  const hiddenClosed = closed.length - closedShown.length;
  if (!openLots.length && !closed.length) {
    return <p className="text-xs text-white/45">No open lots or closed trades on this tape.</p>;
  }
  return (
    <ul className="mt-1 space-y-1 min-w-0">
      {openLots.map(({ n, lot }) => (
        <li
          key={`open-${lot.account ?? ""}-${lot.lot_id}`}
          className="text-xs leading-relaxed text-white/75 min-w-0 break-all [overflow-wrap:anywhere]"
        >
          <span className="text-sky-300 font-medium">Trade {n} open</span>
          {" · "}entry {fmt(lot.entry)} · stop {fmt(lot.stop)} · TP {fmt(lot.take_profit)}
        </li>
      ))}
      {closedShown.map(({ n, trade }) => (
        <li
          key={`closed-${trade.account ?? ""}-${trade.id}`}
          className="text-xs leading-relaxed text-white/75 min-w-0 break-all [overflow-wrap:anywhere]"
        >
          <span className="text-white/90 font-medium">Trade {n} closed</span>
          {" · "}open {fmt(trade.entry_price)} · close{" "}
          {trade.exit_price != null ? fmt(trade.exit_price) : "—"}
          {trade.pnl_pct != null ? ` · ${fmtPct(trade.pnl_pct)}` : ""}
        </li>
      ))}
      {hiddenClosed > 0 ? (
        <li className="text-xs text-white/40">+ {hiddenClosed} older closed trades on this window</li>
      ) : null}
    </ul>
  );
}

/** One champion's BTC/ETH 5m tape. Mount only when that account is in view. */
export default function ChampionTape({
  championName,
  compact = false,
}: {
  championName: string;
  compact?: boolean;
}) {
  const [symbol, setSymbol] = useState<ChartSymbol>("BTC/USDT");

  const candles = useQuery({
    queryKey: ["candles", symbol, CANDLE_LIMIT],
    queryFn: () => api.candles(symbol, "5m", CANDLE_LIMIT),
    refetchInterval: REFRESH_MS,
    enabled: Boolean(championName),
  });
  const live = useQuery({
    queryKey: ["live"],
    queryFn: api.live,
    refetchInterval: REFRESH_MS,
    enabled: Boolean(championName),
  });
  const trades = useQuery({
    queryKey: ["trades", symbol],
    queryFn: () => api.trades(symbol, compact ? 80 : 200),
    refetchInterval: REFRESH_MS,
    enabled: Boolean(championName),
  });

  const bars = candles.data?.candles;
  const openNumbered = useMemo(
    () => numberOpenLots(lotsForChampionSymbol(live.data, championName, symbol), symbol),
    [live.data, championName, symbol],
  );
  const closedNumbered = useMemo(() => {
    const forChamp = closedTradesForChampionSymbol(trades.data, championName, symbol);
    const visible = tradesInCandleWindow(forChamp, bars ?? []);
    return numberClosedTrades(visible, symbol);
  }, [trades.data, championName, symbol, bars]);

  return (
    <div className="min-w-0 space-y-2">
      <SymbolToggle symbol={symbol} onChange={setSymbol} />
      {candles.isError ? (
        <Empty>Could not load /api/candles: {String(candles.error)}. Public Binance only — no trading keys.</Empty>
      ) : !(bars && bars.length) ? (
        <Empty>{candles.isLoading ? "Loading 5m candles…" : "No 5m candles returned."}</Empty>
      ) : (
        <PaperChart
          key={`${championName}-${symbol}-${compact ? "mini" : "full"}`}
          symbol={symbol}
          candles={bars}
          openLots={openNumbered}
          closed={closedNumbered}
          compact={compact}
        />
      )}
      <TradeLegend openLots={openNumbered} closed={closedNumbered} compact={compact} />
      {!compact && (
        <p className="text-xs text-white/45 leading-relaxed min-w-0 break-words">
          This tape is {championName} on {symbol} only. Open lots: entry + stop + take-profit (2:1 vs
          stop, same as live). Closed lots: numbered open/close marks — no stop/TP lines. Labels sit
          in the list, not on the candles.
        </p>
      )}
    </div>
  );
}
