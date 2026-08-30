import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, fmt, fmtPct, Badge, Empty } from "../components/ui";
import {
  accountLabel,
  fmtWhen,
  positionEntry,
  positionPnl,
  positionPnlPct,
  positionStop,
} from "../status/format";

export default function Positions() {
  const trades = useQuery({ queryKey: ["trades"], queryFn: api.trades });
  const live = useQuery({ queryKey: ["live"], queryFn: api.live });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  const run = status.data?.running_now;
  const open = live.data?.positions ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="run">Running now</Badge>
        <span className="text-sm text-white/60">
          Open lots on the paper book
          {run ? ` · last cycle ${fmtWhen(run.cycle.last_cycle_at)}` : ""}
          {live.data?.as_of ? ` · prices ${live.data.as_of}` : ""}
        </span>
      </div>

      <Card title={`Open positions (${open.length})`}>
        {!open.length ? (
          <Empty>
            No open lots on the paper book. Heartbeat only manages stops/TP when lots
            exist; it does not open trades. Last heartbeat stamp:{" "}
            {fmtWhen(run?.heartbeat.last_pass_at)}.
          </Empty>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-white/40 text-xs uppercase">
              <tr>
                <th className="text-left py-2">Account</th>
                <th className="text-left">Symbol</th>
                <th className="text-left">Condition</th>
                <th className="text-right">Entry</th>
                <th className="text-right">Stop</th>
                <th className="text-right">Now</th>
                <th className="text-right">P&L</th>
                <th className="text-right">P&L %</th>
              </tr>
            </thead>
            <tbody>
              {open.map((p, i) => {
                const pnl = positionPnl(p);
                const pnlPct = positionPnlPct(p);
                return (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-2 font-mono text-xs">{accountLabel(p.account)}</td>
                    <td className="font-medium">{p.symbol}</td>
                    <td className="text-white/60">{p.condition ?? "—"}</td>
                    <td className="text-right">{fmt(positionEntry(p))}</td>
                    <td className="text-right">{fmt(positionStop(p))}</td>
                    <td className="text-right">{p.current != null ? fmt(p.current) : "—"}</td>
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
        )}
      </Card>

      <Card title="Closed paper trades" aside="same isolated accounts as the live book">
        {!trades.data?.length ? (
          <Empty>No closed trades recorded yet on this paper state.</Empty>
        ) : (
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
                  <td className="py-2 font-mono text-xs">{accountLabel(t.account)}</td>
                  <td className="font-medium">{t.symbol}</td>
                  <td className="text-white/60">{t.condition}</td>
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
        )}
      </Card>
    </div>
  );
}
