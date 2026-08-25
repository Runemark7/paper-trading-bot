import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, fmt, fmtPct, Badge } from "../components/ui";

export default function Positions() {
  const trades = useQuery({ queryKey: ["trades"], queryFn: api.trades });
  const live = useQuery({ queryKey: ["live"], queryFn: api.live });

  return (
    <div className="space-y-6">
      <Card title={`Open positions (${live.data?.positions?.length ?? 0})`}>
        {!live.data?.positions?.length ? (
          <div className="text-white/40 text-sm">No open positions.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-white/40 text-xs uppercase">
              <tr>
                <th className="text-left py-2">Symbol</th>
                <th className="text-left">Condition</th>
                <th className="text-right">Entry</th>
                <th className="text-right">Now</th>
                <th className="text-right">P&L</th>
                <th className="text-right">P&L %</th>
              </tr>
            </thead>
            <tbody>
              {live.data.positions.map((p, i) => (
                <tr key={i} className="border-t border-white/5">
                  <td className="py-2 font-medium">{p.symbol}</td>
                  <td className="text-white/60">{p.condition ?? "—"}</td>
                  <td className="text-right">{fmt(p.entry_price)}</td>
                  <td className="text-right">{p.current != null ? fmt(p.current) : "—"}</td>
                  <td className={`text-right ${(p.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {p.pnl != null ? fmt(p.pnl) : "—"}
                  </td>
                  <td className={`text-right ${(p.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {fmtPct(p.pnl_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Closed trades">
        {!trades.data?.length ? (
          <div className="text-white/40 text-sm">No closed trades yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-white/40 text-xs uppercase">
              <tr>
                <th className="text-left py-2">Symbol</th>
                <th className="text-left">Condition</th>
                <th className="text-right">Entry</th>
                <th className="text-right">Exit</th>
                <th className="text-left">Reason</th>
                <th className="text-right">P&L %</th>
              </tr>
            </thead>
            <tbody>
              {trades.data.map((t) => (
                <tr key={t.id} className="border-t border-white/5">
                  <td className="py-2 font-medium">{t.symbol}</td>
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