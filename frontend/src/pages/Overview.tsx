import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, Stat, fmt, fmtPct, Badge } from "../components/ui";

export default function Overview() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const live = useQuery({ queryKey: ["live"], queryFn: api.live });
  const regime = useQuery({ queryKey: ["regime"], queryFn: api.regime });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 60_000 });

  const s = summary.data;
  const l = live.data;
  const r = regime.data;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Badge tone={health.data?.ok ? "pos" : "neg"}>
          {health.data?.ok ? "service UP" : "service DOWN"}
        </Badge>
        <Badge tone={r ? (r.zone === "RISK_OFF" ? "neg" : r.zone === "NEUTRAL" ? "warn" : "pos") : "neutral"}>
          regime: {r?.zone ?? "…"}
        </Badge>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Stat label="Live equity" value={l ? fmt(l.live_equity, 0) : "…"} />
        <Stat label="Closed trades" value={s ? String(s.closed_trades) : "…"} />
        <Stat label="Win rate" value={s ? fmtPct(s.win_rate) : "…"} />
        <Stat
          label="Total P&L"
          value={s?.total_pnl != null ? fmt(s.total_pnl) : "—"}
          tone={s?.total_pnl ? (s.total_pnl >= 0 ? "pos" : "neg") : "neutral"}
        />
      </div>

      <Card title="Open positions">
        {!l?.positions?.length ? (
          <div className="text-white/40 text-sm">No open positions.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-white/40 text-xs uppercase">
                <tr><th className="text-left py-2">Symbol</th><th>Condition</th><th>Entry</th><th>Now</th><th>P&L %</th></tr>
              </thead>
              <tbody>
                {l.positions.map((p, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-2 font-medium">{p.symbol}</td>
                    <td className="text-white/60">{p.condition ?? "—"}</td>
                    <td className="text-right">{fmt(p.entry_price)}</td>
                    <td className="text-right">{p.current != null ? fmt(p.current) : "—"}</td>
                    <td className={`text-right ${(p.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmtPct(p.pnl_pct)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}