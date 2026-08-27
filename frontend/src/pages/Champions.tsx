import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchChampions, fetchGraduated, fetchDiscovery } from "../api/client";
import { Card, fmt, Badge } from "../components/ui";

export default function Champions() {
  const qChamps = useQuery({ queryKey: ["champions"], queryFn: fetchChampions, refetchInterval: 30_000 });
  const qGrad = useQuery({ queryKey: ["graduated"], queryFn: fetchGraduated, refetchInterval: 30_000 });
  const qDisc = useQuery({ queryKey: ["discovery"], queryFn: fetchDiscovery, refetchInterval: 30_000 });

  const [expandedStrat, setExpandedStrat] = useState<string | null>(null);

  const champs = qChamps.data?.active_champions ?? [];
  const targetMax = qChamps.data?.target_active ?? 10;
  const graduated = qGrad.data ?? [];
  const discoveryLog = qDisc.data ?? [];

  return (
    <div className="space-y-6">
      {/* 1. Active Champions in Testing */}
      <Card title={`Active Live Testing Pool (${champs.length} / ${targetMax})`}>
        <div className="text-sm text-white/60 mb-3">
          Strategies are tested on live isolated $10,000 accounts. Once a strategy completes <b>10 closed entries</b>,
          it graduates to the production board below with full trade execution logs.
        </div>

        {qChamps.error && <div className="text-rose-400 text-sm">Error: {String(qChamps.error)}</div>}

        {!champs.length ? (
          <div className="text-white/40 text-sm">No champions currently testing.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-white/40 text-xs uppercase">
                <tr>
                  <th className="text-left py-2">Strategy</th>
                  <th className="text-right">Closed (Target: 10)</th>
                  <th className="text-right">Wins</th>
                  <th className="text-right">Live P&L</th>
                  <th className="text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {champs.map((c) => (
                  <tr key={c.name} className="border-t border-white/5">
                    <td className="py-2 font-mono text-sm">{c.name}</td>
                    <td className="text-right">{c.closed} / 10</td>
                    <td className="text-right">{c.wins}</td>
                    <td className={`text-right font-medium ${c.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt(c.pnl)}
                    </td>
                    <td>
                      <Badge tone="neutral">evaluating</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* 2. Graduated Production Ready Strategies */}
      <Card title={`🏆 Graduated Strategies (${graduated.length}) — 10/10 Trades Completed`}>
        <div className="text-sm text-white/60 mb-3">
          Evaluated strategies ready for live production deployment with complete trade breakdown.
        </div>

        {!graduated.length ? (
          <div className="text-white/40 text-sm">
            No strategies have completed 10 trades yet. Results will automatically appear here once evaluated.
          </div>
        ) : (
          <div className="space-y-4">
            {graduated.map((g) => {
              const isExpanded = expandedStrat === g.name;
              return (
                <div key={g.name} className="border border-white/10 rounded-lg p-4 bg-white/[0.02]">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-mono text-base font-semibold text-white">{g.name}</div>
                      <div className="text-xs text-white/50 mt-1">
                        Graduated: {new Date(g.graduated_at).toLocaleString()} · {g.closed_trades} trades evaluated
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <div className={`text-base font-bold ${g.total_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {fmt(g.total_pnl)}
                        </div>
                        <div className="text-xs text-white/60">Win Rate: {g.win_rate_pct}%</div>
                      </div>
                      <Badge tone={g.status === "READY_FOR_LIVE" ? "pos" : "neg"}>{g.status}</Badge>
                      <button
                        onClick={() => setExpandedStrat(isExpanded ? null : g.name)}
                        className="px-3 py-1 text-xs bg-white/10 hover:bg-white/20 rounded text-white transition"
                      >
                        {isExpanded ? "Hide History" : "View 10 Trades"}
                      </button>
                    </div>
                  </div>

                  {/* Expanded Trade History Breakdown */}
                  {isExpanded && (
                    <div className="mt-4 pt-4 border-t border-white/10 overflow-x-auto">
                      <div className="text-xs font-semibold uppercase text-white/40 mb-2">Detailed Trade History</div>
                      <table className="w-full text-xs">
                        <thead className="text-white/40 uppercase">
                          <tr>
                            <th className="text-left py-1">Symbol</th>
                            <th className="text-left">Entry Time</th>
                            <th className="text-right">Entry $</th>
                            <th className="text-right">Exit $</th>
                            <th className="text-right">Qty</th>
                            <th className="text-right">P&L</th>
                            <th className="text-right">Return</th>
                            <th className="text-left">Exit Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {g.trade_history?.map((t, idx) => (
                            <tr key={idx} className="border-t border-white/5">
                              <td className="py-1 font-medium">{t.symbol}</td>
                              <td className="text-white/60">{t.entry_ts?.replace("T", " ").slice(0, 16)}</td>
                              <td className="text-right font-mono">${t.entry_price.toLocaleString()}</td>
                              <td className="text-right font-mono">${t.exit_price.toLocaleString()}</td>
                              <td className="text-right font-mono">{t.size.toFixed(4)}</td>
                              <td className={`text-right font-bold ${t.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                {fmt(t.pnl)}
                              </td>
                              <td className={`text-right ${t.pnl_pct >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                {(t.pnl_pct * 100).toFixed(2)}%
                              </td>
                              <td className="text-white/70">{t.exit_reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* 3. Continuous Backtest Discovery Stream */}
      <Card title={`🔍 Continuous Strategy Backtest Log (Latest ${discoveryLog.length} Evaluations)`}>
        <div className="text-sm text-white/60 mb-3">
          Real-time stream of candidate strategy rules being backtested on 5m candles across out-of-sample periods.
        </div>

        {!discoveryLog.length ? (
          <div className="text-white/40 text-sm">No discovery backtests recorded yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-white/40 uppercase">
                <tr>
                  <th className="text-left py-1">Strategy Rule</th>
                  <th className="text-left">Tested At</th>
                  <th className="text-right">Win Rate</th>
                  <th className="text-right">Sharpe</th>
                  <th className="text-right">Train P&L</th>
                  <th className="text-right">Test P&L</th>
                  <th className="text-left">Result</th>
                </tr>
              </thead>
              <tbody>
                {discoveryLog.slice(0, 40).map((d, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-1 font-mono font-medium text-white">{d.strategy}</td>
                    <td className="text-white/50">{d.tested_at?.replace("T", " ").slice(0, 16)}</td>
                    <td className="text-right">{d.win_rate_pct}%</td>
                    <td className="text-right font-mono">{d.sharpe.toFixed(2)}</td>
                    <td className={`text-right font-mono ${d.train_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt(d.train_pnl)}
                    </td>
                    <td className={`text-right font-mono font-bold ${d.test_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt(d.test_pnl)}
                    </td>
                    <td>
                      <Badge tone={d.qualified ? "pos" : "neg"}>
                        {d.qualified ? "QUALIFIED" : "REJECTED"}
                      </Badge>
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