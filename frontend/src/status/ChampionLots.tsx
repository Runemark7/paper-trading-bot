import type { OpenLot } from "../api/types";
import { Card, DesktopTable, Empty, Field, FieldGrid, PhoneCards, fmt } from "../components/ui";
import { LotHealthChips } from "./LotHealth";
import { fmtChampionSince, lotUnrealized } from "./format";

export function ChampionSinceChip({ since }: { since?: string | null }) {
  const label = fmtChampionSince(since);
  return (
    <span
      className="inline-flex max-w-full min-w-0 items-center rounded-md bg-white/10 px-1.5 py-0.5 text-[11px] font-medium text-white/80 break-words whitespace-normal [overflow-wrap:anywhere]"
      aria-label={`Champion since ${label}`}
    >
      Champion since {label}
    </span>
  );
}

/** Always-visible BTC vs ETH lot counts. Short chips so they wrap on a phone. */
export function PairLotChips({ btc, eth }: { btc: number | string; eth: number | string }) {
  return (
    <div
      className="flex flex-wrap items-center gap-1 min-w-0"
      aria-label={`Open lots BTC ${btc} ETH ${eth}`}
    >
      <span className="inline-flex shrink-0 items-center rounded-md bg-white/10 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white/80">
        BTC {btc}
      </span>
      <span className="inline-flex shrink-0 items-center rounded-md bg-white/10 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white/80">
        ETH {eth}
      </span>
    </div>
  );
}

export function ChampionLotList({ lots }: { lots: OpenLot[] }) {
  return (
    <>
      <p className="text-xs text-white/45 leading-relaxed min-w-0 break-words">
        Each row is one lot. Signal is the latest 5m predicate (on vs would exit). Path is
        where now sits between this lot&apos;s stop and 2:1 TP — mid is the middle of that
        range, not a third strategy state.
      </p>
      <PhoneCards>
        {lots.map((lot, i) => {
          const pnl = lotUnrealized(lot);
          return (
            <li
              key={`${lot.account ?? ""}-${lot.symbol}-${lot.lot_id}-${i}`}
              className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden"
            >
              <div className="flex items-baseline justify-between gap-2 min-w-0">
                <span className="font-medium min-w-0 truncate">{lot.symbol}</span>
                <span className={`shrink-0 ${(pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {pnl != null ? fmt(pnl) : "—"}
                </span>
              </div>
              <LotHealthChips lot={lot} extra />
              <FieldGrid>
                <Field label="Condition" span mono>
                  {lot.condition ?? "—"}
                </Field>
                <Field label="Entry">{fmt(lot.entry)}</Field>
                <Field label="Stop">{fmt(lot.stop)}</Field>
                <Field label="Now">{lot.current != null ? fmt(lot.current) : "—"}</Field>
                <Field label="P&L">
                  <span className={(pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}>
                    {pnl != null ? fmt(pnl) : "—"}
                  </span>
                </Field>
              </FieldGrid>
            </li>
          );
        })}
      </PhoneCards>
      <DesktopTable>
        <table className="w-full text-sm">
          <thead className="text-white/40 text-xs uppercase">
            <tr>
              <th className="text-left py-2">Symbol</th>
              <th className="text-left">Health</th>
              <th className="text-left">Condition</th>
              <th className="text-right">Entry</th>
              <th className="text-right">Stop</th>
              <th className="text-right">Now</th>
              <th className="text-right">P&L</th>
            </tr>
          </thead>
          <tbody>
            {lots.map((lot, i) => {
              const pnl = lotUnrealized(lot);
              return (
                <tr
                  key={`${lot.account ?? ""}-${lot.symbol}-${lot.lot_id}-${i}`}
                  className="border-t border-white/5"
                >
                  <td className="py-2 font-medium">{lot.symbol}</td>
                  <td className="py-2">
                    <LotHealthChips lot={lot} extra />
                  </td>
                  <td className="text-white/60 font-mono text-xs break-all">{lot.condition ?? "—"}</td>
                  <td className="text-right">{fmt(lot.entry)}</td>
                  <td className="text-right">{fmt(lot.stop)}</td>
                  <td className="text-right">{lot.current != null ? fmt(lot.current) : "—"}</td>
                  <td className={`text-right ${(pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {pnl != null ? fmt(pnl) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </DesktopTable>
    </>
  );
}

export function ChampionOpenLots({
  lots,
  liveReady,
  knownCount,
}: {
  lots: OpenLot[];
  liveReady: boolean;
  knownCount: number;
}) {
  if (lots.length) return <ChampionLotList lots={lots} />;
  if (liveReady || knownCount === 0) {
    return <p className="text-sm text-white/50">no open lots</p>;
  }
  return <Empty>Loading open lots from /api/live…</Empty>;
}

export function ChampionOpenLotsCard({
  lots,
  liveReady,
  knownCount,
}: {
  lots: OpenLot[];
  liveReady: boolean;
  knownCount: number;
}) {
  return (
    <Card title={`Open lots · ${lots.length || knownCount}`}>
      <ChampionOpenLots lots={lots} liveReady={liveReady} knownCount={knownCount} />
    </Card>
  );
}
