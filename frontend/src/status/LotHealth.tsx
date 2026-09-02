import type { ReactNode } from "react";
import type { OpenLot } from "../api/types";
import {
  formatGate,
  lotHealthSummary,
  pathLabel,
  pathTone,
  signalLabel,
  signalTone,
} from "./format";

const chipTone = {
  pos: "bg-emerald-500/15 text-emerald-300",
  neg: "bg-rose-500/15 text-rose-300",
  warn: "bg-amber-500/15 text-amber-300",
  neutral: "bg-white/10 text-white/80",
} as const;

function Chip({
  children,
  tone,
}: {
  children: ReactNode;
  tone: keyof typeof chipTone;
}) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium tabular-nums ${chipTone[tone]}`}
    >
      {children}
    </span>
  );
}

/** Per-lot signal + path chips. Mid = middle of stop–TP, not a third strategy state. */
export function LotHealthChips({
  lot,
  extra = false,
}: {
  lot: OpenLot;
  extra?: boolean;
}) {
  const gate = extra ? formatGate(lot.gate) : null;
  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      <Chip tone={signalTone(lot.signal)}>{signalLabel(lot.signal)}</Chip>
      <Chip tone={pathTone(lot.path)}>{pathLabel(lot.path)}</Chip>
      {gate ? <Chip tone="neutral">{gate}</Chip> : null}
    </div>
  );
}

/** Collapsed-card flags only: would-exit / near-stop / near-TP. Zeros omitted. */
export function LotHealthSummaryChips({ lots }: { lots: OpenLot[] }) {
  const { wouldExit, nearStop, nearTp } = lotHealthSummary(lots);
  if (!wouldExit && !nearStop && !nearTp) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 min-w-0">
      {wouldExit > 0 ? <Chip tone="neg">{wouldExit} would exit</Chip> : null}
      {nearStop > 0 ? <Chip tone="warn">{nearStop} near stop</Chip> : null}
      {nearTp > 0 ? <Chip tone="pos">{nearTp} near TP</Chip> : null}
    </div>
  );
}
