import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  createChart,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { CandleBar } from "../api/types";
import type { NumberedClosedTrade, NumberedOpenLot } from "./numberTrades";

const BG = "#0b1020";
const ENTRY = "#38bdf8";
const STOP = "#fb7185";
const TP = "#34d399";
const UP = "#34d399";
const DOWN = "#fb7185";

function isoToMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const d = Date.parse(iso);
  return Number.isNaN(d) ? null : d;
}

/** Snap a wall-clock time onto a 5m bar that exists in the series (markers need that).
 *  Times left of the loaded window stay off-chart — do not glue them to bar 0. */
function snapToCandle(ms: number, candleSec: number[]): UTCTimestamp | null {
  if (!candleSec.length) return null;
  const t = Math.floor(ms / 1000);
  if (t < candleSec[0]) return null;
  let best = candleSec[0];
  for (const ct of candleSec) {
    if (ct <= t) best = ct;
    else break;
  }
  return best as UTCTimestamp;
}

export default function PaperChart({
  candles,
  openLots,
  closed,
  symbol,
  compact = false,
}: {
  candles: CandleBar[];
  openLots: NumberedOpenLot[];
  closed: NumberedClosedTrade[];
  symbol: string;
  compact?: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const fittedFor = useRef<string | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const chart = createChart(host, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: BG },
        textColor: "#cbd5e1",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.06)" },
        horzLines: { color: "rgba(255,255,255,0.06)" },
      },
      rightPriceScale: {
        borderColor: "rgba(255,255,255,0.12)",
        scaleMargins: { top: 0.16, bottom: 0.08 },
      },
      timeScale: {
        borderColor: "rgba(255,255,255,0.12)",
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: { vertTouchDrag: false },
    });
    const series = chart.addCandlestickSeries({
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      linesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    if (!candles.length) {
      series.setData([]);
      series.setMarkers([]);
      for (const line of linesRef.current) series.removePriceLine(line);
      linesRef.current = [];
      return;
    }

    const data = candles.map((c) => ({
      time: Math.floor(c.t / 1000) as UTCTimestamp,
      open: c.o,
      high: c.h,
      low: c.l,
      close: c.c,
    }));
    series.setData(data);
    const candleSec = data.map((d) => d.time as number);

    for (const line of linesRef.current) series.removePriceLine(line);
    linesRef.current = [];
    // Open lots only: entry + stop + TP. Titles live in the list under the chart
    // so twelve axis tags do not stack on the candles.
    for (const { lot } of openLots) {
      linesRef.current.push(
        series.createPriceLine({
          price: lot.entry,
          color: ENTRY,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: false,
          title: "",
        }),
      );
      linesRef.current.push(
        series.createPriceLine({
          price: lot.stop,
          color: STOP,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "",
        }),
      );
      linesRef.current.push(
        series.createPriceLine({
          price: lot.take_profit,
          color: TP,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: false,
          title: "",
        }),
      );
    }

    const markers: SeriesMarker<Time>[] = [];
    for (const { n, lot } of openLots) {
      const ms = isoToMs(lot.entry_ts);
      const time = ms != null ? snapToCandle(ms, candleSec) : null;
      if (time == null) continue;
      markers.push({
        time,
        position: "belowBar",
        color: ENTRY,
        shape: "arrowUp",
        text: `${n}`,
      });
    }
    // Closed lots: open/close marks only — no stop/TP lines.
    for (const { n, trade } of closed) {
      const openMs = isoToMs(trade.entry_ts);
      const closeMs = isoToMs(trade.exit_ts);
      const openT = openMs != null ? snapToCandle(openMs, candleSec) : null;
      const closeT = closeMs != null ? snapToCandle(closeMs, candleSec) : null;
      const win = (trade.pnl_pct ?? 0) >= 0;
      if (openT != null) {
        markers.push({
          time: openT,
          position: "belowBar",
          color: ENTRY,
          shape: "arrowUp",
          text: `${n}`,
        });
      }
      if (closeT != null) {
        markers.push({
          time: closeT,
          position: "aboveBar",
          color: win ? TP : STOP,
          shape: "arrowDown",
          text: `${n}`,
        });
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);

    const fitKey = `${symbol}-${candles.length}`;
    if (fittedFor.current !== fitKey) {
      chart.timeScale().fitContent();
      fittedFor.current = fitKey;
    }
  }, [candles, openLots, closed, symbol]);

  return (
    <div
      ref={hostRef}
      className={
        compact
          ? "w-full min-h-[200px] h-[240px] rounded-lg overflow-hidden bg-[#0b1020]"
          : "w-full min-h-[280px] h-[min(52vh,440px)] rounded-lg overflow-hidden bg-[#0b1020]"
      }
      role="img"
      aria-label={`${symbol} 5m paper chart`}
    />
  );
}
