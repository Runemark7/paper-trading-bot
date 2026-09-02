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

/** Snap a wall-clock time onto a 5m bar that exists in the series (markers need that). */
function snapToCandle(ms: number, candleSec: number[]): UTCTimestamp | null {
  if (!candleSec.length) return null;
  const t = Math.floor(ms / 1000);
  if (t <= candleSec[0]) return candleSec[0] as UTCTimestamp;
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
}: {
  candles: CandleBar[];
  openLots: NumberedOpenLot[];
  closed: NumberedClosedTrade[];
  symbol: string;
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
    for (const { n, lot } of openLots) {
      linesRef.current.push(
        series.createPriceLine({
          price: lot.entry,
          color: ENTRY,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          axisLabelVisible: true,
          title: `${n} entry`,
        }),
      );
      linesRef.current.push(
        series.createPriceLine({
          price: lot.stop,
          color: STOP,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `${n} stop`,
        }),
      );
      linesRef.current.push(
        series.createPriceLine({
          price: lot.take_profit,
          color: TP,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: `${n} TP`,
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
        text: `${n} open`,
      });
    }
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
          text: `${n} open`,
        });
      }
      if (closeT != null) {
        markers.push({
          time: closeT,
          position: "aboveBar",
          color: win ? TP : STOP,
          shape: "arrowDown",
          text: `${n} close`,
        });
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);

    if (fittedFor.current !== symbol) {
      chart.timeScale().fitContent();
      fittedFor.current = symbol;
    }
  }, [candles, openLots, closed, symbol]);

  return (
    <div
      ref={hostRef}
      className="w-full min-h-[280px] h-[min(55vh,440px)] rounded-lg overflow-hidden bg-[#0b1020]"
      role="img"
      aria-label={`${symbol} 5m paper chart`}
    />
  );
}
