import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ChartSymbol } from "../api/types";
import { Badge, Card, Empty, Field, FieldGrid, fmt, fmtPct, MonoName } from "../components/ui";
import PaperChart from "../chart/PaperChart";
import { lotsFromLive, numberClosedTrades, numberOpenLots } from "../chart/numberTrades";
import { accountLabel, fmtWhen } from "../status/format";

const SYMBOLS: ChartSymbol[] = ["BTC/USDT", "ETH/USDT"];
const REFRESH_MS = 20_000;

export default function ChartPage() {
  const [symbol, setSymbol] = useState<ChartSymbol>("BTC/USDT");

  const candles = useQuery({
    queryKey: ["candles", symbol],
    queryFn: () => api.candles(symbol, "5m"),
    refetchInterval: REFRESH_MS,
  });
  const live = useQuery({
    queryKey: ["live"],
    queryFn: api.live,
    refetchInterval: REFRESH_MS,
  });
  const trades = useQuery({
    queryKey: ["trades", symbol],
    queryFn: () => api.trades(symbol, 200),
    refetchInterval: REFRESH_MS,
  });

  const openNumbered = useMemo(
    () => numberOpenLots(lotsFromLive(live.data, symbol), symbol),
    [live.data, symbol],
  );
  const closedNumbered = useMemo(
    () => numberClosedTrades(trades.data, symbol),
    [trades.data, symbol],
  );

  const bars = candles.data?.candles ?? [];
  const other = SYMBOLS.find((s) => s !== symbol);

  return (
    <div className="space-y-4 min-w-0">
      <div className="flex flex-wrap items-start gap-2">
        <Badge tone="run">Running now</Badge>
        <span className="text-sm text-white/60 min-w-0 break-words">
          Paper book · 5m public Binance tape · refreshes every 20s (no invented ticks)
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2" role="group" aria-label="Chart symbol">
        {SYMBOLS.map((s) => {
          const on = s === symbol;
          return (
            <button
              key={s}
              type="button"
              aria-pressed={on}
              onClick={() => setSymbol(s)}
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

      <Card
        title={`${symbol} · 5m`}
        aside={candles.data?.as_of ? `ohlcv ${candles.data.as_of}` : live.data?.as_of ? `prices ${live.data.as_of}` : undefined}
      >
        {candles.isError ? (
          <Empty>Could not load /api/candles: {String(candles.error)}. Public Binance only — no trading keys.</Empty>
        ) : !bars.length ? (
          <Empty>{candles.isLoading ? "Loading 5m candles…" : "No 5m candles returned."}</Empty>
        ) : (
          <PaperChart
            key={symbol}
            symbol={symbol}
            candles={bars}
            openLots={openNumbered}
            closed={closedNumbered}
          />
        )}
        <p className="mt-2 text-xs text-white/45 leading-relaxed">
          Open lots: solid entry, dashed stop, dashed take-profit (2:1 vs stop, same as live).
          Closed lots: numbered open and close marks. {other} is hidden until you toggle.
        </p>
      </Card>

      <Card title={`Open lots on ${symbol} (${openNumbered.length})`}>
        {!openNumbered.length ? (
          <Empty>No open {symbol} lots on the paper book. Toggle {other} or wait for the next cycle.</Empty>
        ) : (
          <ul className="space-y-3 min-w-0">
            {openNumbered.map(({ n, lot }) => (
              <li key={`${lot.account ?? ""}-${lot.lot_id}`} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                <div className="flex items-baseline justify-between gap-2 min-w-0">
                  <span className="font-medium">Trade {n} open</span>
                  <MonoName className="text-xs text-white/50">{accountLabel(lot.account)}</MonoName>
                </div>
                <FieldGrid>
                  <Field label="Entry">{fmt(lot.entry)}</Field>
                  <Field label="Stop">{fmt(lot.stop)}</Field>
                  <Field label="Take-profit">{fmt(lot.take_profit)}</Field>
                  <Field label="Lot">{lot.lot_id}</Field>
                  {lot.condition ? (
                    <Field label="Condition" span mono>
                      {lot.condition}
                    </Field>
                  ) : null}
                </FieldGrid>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title={`Closed trades on ${symbol} (${closedNumbered.length})`}>
        {!closedNumbered.length ? (
          <Empty>No closed {symbol} lots in the recent paper history.</Empty>
        ) : (
          <ul className="space-y-3 min-w-0">
            {closedNumbered.map(({ n, trade }) => (
              <li key={`${trade.account ?? ""}-${trade.id}`} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                <div className="flex items-baseline justify-between gap-2 min-w-0">
                  <span className="font-medium">Trade {n}</span>
                  <span className={`shrink-0 ${(trade.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {fmtPct(trade.pnl_pct)}
                  </span>
                </div>
                <MonoName className="block text-xs text-white/50">{accountLabel(trade.account)}</MonoName>
                <FieldGrid>
                  <Field label={`${n} open`}>{fmt(trade.entry_price)}</Field>
                  <Field label={`${n} close`}>{trade.exit_price != null ? fmt(trade.exit_price) : "—"}</Field>
                  <Field label="Opened" span>
                    {fmtWhen(trade.entry_ts)}
                  </Field>
                  <Field label="Closed" span>
                    {fmtWhen(trade.exit_ts)}
                  </Field>
                  <Field label="Reason" span>
                    {trade.exit_reason ?? "—"}
                  </Field>
                </FieldGrid>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
