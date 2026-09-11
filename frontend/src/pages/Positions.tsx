import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, fmt, fmtPct, Badge, Empty, MonoName, Field, FieldGrid, PhoneCards, DesktopTable } from "../components/ui";
import { LotHealthChips } from "../status/LotHealth";
import {
  accountLabel,
  flatOpenLots,
  fmtWhen,
  lotUnrealized,
  openLotsTotal,
} from "../status/format";

export default function Positions() {
  const trades = useQuery({ queryKey: ["trades"], queryFn: () => api.trades() });
  const live = useQuery({ queryKey: ["live"], queryFn: api.live });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  const run = status.data?.running_now;
  const lots = flatOpenLots(live.data);
  const openLots = openLotsTotal(live.data, run?.open_lots ?? run?.positions_open);

  return (
    <div className="space-y-6 min-w-0">
      <div className="flex flex-wrap items-start gap-2">
        <Badge tone="run">Running now</Badge>
        <span className="text-sm text-white/60 min-w-0 break-words">
          Open lots on the paper book
          {run ? ` · last cycle ${fmtWhen(run.cycle.last_cycle_at)}` : ""}
          {live.data?.as_of ? ` · prices ${live.data.as_of}` : ""}
        </span>
      </div>

      <Card title={`Open lots (${openLots})`}>
        {!lots.length ? (
          <Empty>
            No open lots on the paper book. Heartbeat only manages stops/TP when lots
            exist; it does not open trades. Last heartbeat stamp:{" "}
            {fmtWhen(run?.heartbeat.last_pass_at)}.
          </Empty>
        ) : (
          <>
            <p className="text-xs text-white/45 mb-3 leading-relaxed min-w-0 break-words">
              One card per lot. Signal is still-long vs would-exit on the latest 5m close.
              Path is now between that lot&apos;s stop and 2:1 TP (mid = middle of the range,
              not a third strategy state).
            </p>
            <PhoneCards>
              {lots.map((lot, i) => {
                const pnl = lotUnrealized(lot);
                const pnlPct = lot.unrealized_pct;
                return (
                  <li
                    key={`${lot.account ?? ""}-${lot.symbol}-${lot.lot_id}-${i}`}
                    className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden"
                  >
                    <div className="flex items-baseline justify-between gap-2 min-w-0">
                      <span className="font-medium min-w-0 truncate">{lot.symbol}</span>
                      <span className={`shrink-0 ${(pnlPct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {fmtPct(pnlPct)}
                      </span>
                    </div>
                    <MonoName className="block text-xs text-white/50">{accountLabel(lot.account)}</MonoName>
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
                    <th className="text-left py-2">Account</th>
                    <th className="text-left">Symbol</th>
                    <th className="text-left">Health</th>
                    <th className="text-left">Condition</th>
                    <th className="text-right">Entry</th>
                    <th className="text-right">Stop</th>
                    <th className="text-right">Now</th>
                    <th className="text-right">P&L</th>
                    <th className="text-right">P&L %</th>
                  </tr>
                </thead>
                <tbody>
                  {lots.map((lot, i) => {
                    const pnl = lotUnrealized(lot);
                    const pnlPct = lot.unrealized_pct;
                    return (
                      <tr
                        key={`${lot.account ?? ""}-${lot.symbol}-${lot.lot_id}-${i}`}
                        className="border-t border-white/5"
                      >
                        <td className="py-2">
                          <MonoName className="text-xs">{accountLabel(lot.account)}</MonoName>
                        </td>
                        <td className="font-medium">{lot.symbol}</td>
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
                        <td className={`text-right ${(pnlPct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {fmtPct(pnlPct)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </DesktopTable>
          </>
        )}
      </Card>

      <Card title="Closed paper trades" aside="same isolated accounts as the live book">
        {!trades.data?.length ? (
          <Empty>No closed trades recorded yet on this paper state.</Empty>
        ) : (
          <>
            <PhoneCards>
              {trades.data.map((t) => (
                <li key={`${t.account ?? ""}-${t.id}`} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                  <div className="flex items-baseline justify-between gap-2 min-w-0">
                    <span className="font-medium min-w-0 truncate">{t.symbol}</span>
                    <span className={`shrink-0 ${(t.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmtPct(t.pnl_pct)}
                    </span>
                  </div>
                  <MonoName className="block text-xs text-white/50">{accountLabel(t.account)}</MonoName>
                  <FieldGrid>
                    <Field label="Condition" span mono>
                      {t.condition}
                    </Field>
                    <Field label="Entry">{fmt(t.entry_price)}</Field>
                    <Field label="Exit">{t.exit_price != null ? fmt(t.exit_price) : "—"}</Field>
                    <Field label="Reason" span>
                      {t.exit_reason ?? "—"}
                    </Field>
                  </FieldGrid>
                </li>
              ))}
            </PhoneCards>
            <DesktopTable>
              <table className="w-full text-sm">
                <thead className="text-white/40 text-xs uppercase">
                  <tr>
                    <th className="text-left py-2">Account</th>
                    <th className="text-left">Symbol</th>
                    <th className="text-left">Condition</th>
                    <th className="text-right">Entry</th>
                    <th className="text-right">Exit</th>
                    <th className="text-left">Reason</th>
                    <th className="text-right">P&L %</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.data.map((t) => (
                    <tr key={`${t.account ?? ""}-${t.id}`} className="border-t border-white/5">
                      <td className="py-2">
                        <MonoName className="text-xs">{accountLabel(t.account)}</MonoName>
                      </td>
                      <td className="font-medium">{t.symbol}</td>
                      <td className="text-white/60 font-mono text-xs break-all">{t.condition}</td>
                      <td className="text-right">{fmt(t.entry_price)}</td>
                      <td className="text-right">{t.exit_price != null ? fmt(t.exit_price) : "—"}</td>
                      <td className="text-white/60">{t.exit_reason ?? "—"}</td>
                      <td className={`text-right ${(t.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {fmtPct(t.pnl_pct)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </DesktopTable>
          </>
        )}
      </Card>
    </div>
  );
}
