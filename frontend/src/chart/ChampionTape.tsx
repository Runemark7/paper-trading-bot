import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ChartSymbol } from "../api/types";
import { Empty, fmt, fmtPct } from "../components/ui";
import { LotHealthChips } from "../status/LotHealth";
import { openLotsByPair } from "../status/format";
import PaperChart from "./PaperChart";
import {
  candleLimitForEntries,
  closedTradesForChampionSymbol,
  entryTimesForTape,
  lotsForChampionSymbol,
  lotsOlderThanTape,
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
  btcLots,
  ethLots,
}: {
  symbol: ChartSymbol;
  onChange: (s: ChartSymbol) => void;
  btcLots: number;
  ethLots: number;
}) {
  return (
    <div className="grid grid-cols-2 gap-2" role="group" aria-label="Chart symbol">
      {SYMBOLS.map((s) => {
        const on = s === symbol;
        const n = s === "BTC/USDT" ? btcLots : ethLots;
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
            <span className="block text-[11px] font-normal opacity-70 tabular-nums">{n} lots</span>
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
          className="text-xs leading-relaxed text-white/75 min-w-0 space-y-1"
        >
          <div className="flex flex-wrap items-center gap-1 min-w-0">
            <span className="text-sky-300 font-medium shrink-0">Trade {n} open</span>
            <LotHealthChips lot={lot} extra />
          </div>
          <div className="break-all [overflow-wrap:anywhere]">
            entry {fmt(lot.entry)} · stop {fmt(lot.stop)} · TP {fmt(lot.take_profit)}
          </div>
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

  const candleLimit = useMemo(() => {
    const lots = lotsForChampionSymbol(live.data, championName, symbol);
    const closed = closedTradesForChampionSymbol(trades.data, championName, symbol);
    return candleLimitForEntries(entryTimesForTape(lots, closed));
  }, [live.data, trades.data, championName, symbol]);

  const candles = useQuery({
    queryKey: ["candles", symbol, candleLimit],
    queryFn: () => api.candles(symbol, "5m", candleLimit),
    refetchInterval: REFRESH_MS,
    enabled: Boolean(championName),
  });

  const bars = candles.data?.candles;
  const split = useMemo(() => openLotsByPair(live.data, championName), [live.data, championName]);
  const openNumbered = useMemo(
    () => numberOpenLots(lotsForChampionSymbol(live.data, championName, symbol), symbol),
    [live.data, championName, symbol],
  );
  const closedNumbered = useMemo(() => {
    const forChamp = closedTradesForChampionSymbol(trades.data, championName, symbol);
    const visible = tradesInCandleWindow(forChamp, bars ?? []);
    return numberClosedTrades(visible, symbol);
  }, [trades.data, championName, symbol, bars]);
  const offChart = useMemo(
    () => lotsOlderThanTape(openNumbered, bars ?? []),
    [openNumbered, bars],
  );

  return (
    <div className="min-w-0 space-y-2">
      <SymbolToggle
        symbol={symbol}
        onChange={setSymbol}
        btcLots={split.btc}
        ethLots={split.eth}
      />
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
      {offChart.length > 0 ? (
        <p className="text-xs text-white/40 leading-relaxed min-w-0 break-words">
          Trade {offChart.map((x) => x.n).join(", ")} opened before this 7-day tape.
        </p>
      ) : null}
      {!compact && (
        <p className="text-xs text-white/45 leading-relaxed min-w-0 break-words">
          This tape is {championName} on {symbol} only. Open lots: entry + stop + take-profit (2:1 vs
          stop, same as live) plus this-bar signal (on vs would exit) and path (near stop / mid /
          near TP — mid is the middle of stop–TP, not a third strategy state). Closed lots: numbered
          open/close marks — no stop/TP lines. Labels sit in the list, not on the candles.
        </p>
      )}
    </div>
  );
}
