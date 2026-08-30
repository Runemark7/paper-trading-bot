import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchChampions, fetchGraduated, fetchDiscovery, api } from "../api/client";
import { Card, fmt, Badge, Empty } from "../components/ui";
import { certaintyLabel, fmtWhen } from "../status/format";

export default function Champions() {
  const qChamps = useQuery({ queryKey: ["champions"], queryFn: fetchChampions, refetchInterval: 30_000 });
  const qGrad = useQuery({ queryKey: ["graduated"], queryFn: fetchGraduated, refetchInterval: 30_000 });
  const qDisc = useQuery({ queryKey: ["discovery"], queryFn: fetchDiscovery, refetchInterval: 30_000 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 15_000 });

  const [expandedStrat, setExpandedStrat] = useState<string | null>(null);

  const champs = qChamps.data?.active_champions ?? [];
  const targetMax = qChamps.data?.target_active ?? 1000;
  const evalLimit = qChamps.data?.evaluation_limit ?? 25;
  const graduated = qGrad.data ?? [];
  const discoveryLog = qDisc.data ?? [];
  const run = status.data?.running_now;
  const prog = status.data?.in_progress;

  return (
    <div className="space-y-6">
      <p className="text-sm text-white/55">
        Names in the first table are on the live paper book (isolated €10k accounts).
        Discovery and graduation below are last-known pipeline results — not a live job
        unless the sidecar stamp says so.
      </p>

      <Card
        title={`On the paper book (${champs.length} / ${targetMax})`}
        aside={run ? `last cycle ${fmtWhen(run.cycle.last_cycle_at)}` : undefined}
      >
        <div className="text-sm text-white/60 mb-3">
          Each name is an isolated paper account. After <b>{evalLimit} closed entries</b> the
          account leaves this list. <code>GRADUATED_PAPER</code> is graduated paper, not live money.
          {run?.strategy.mode === "sma_stack_fallback" && (
            <> Pool empty — book is running <code>sma_stack</code> fallback.</>
          )}
        </div>

        {qChamps.error && <div className="text-rose-400 text-sm">Error: {String(qChamps.error)}</div>}

        {!champs.length ? (
          <Empty>
            No champions in champions.json. The live book falls back to{" "}
            <code>{run?.strategy.fallback ?? "sma_stack"}</code> until discovery admits names.
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-white/40 text-xs uppercase">
                <tr>
                  <th className="text-left py-2">Strategy</th>
                  <th className="text-right">Closed (of {evalLimit})</th>
                  <th className="text-right">Wins</th>
                  <th className="text-right">Paper P&L</th>
                  <th className="text-left">On book</th>
                </tr>
              </thead>
              <tbody>
                {champs.map((c) => (
                  <tr key={c.name} className="border-t border-white/5">
                    <td className="py-2 font-mono text-sm">{c.name}</td>
                    <td className="text-right">{c.closed} / {evalLimit}</td>
                    <td className="text-right">{c.wins}</td>
                    <td className={`text-right font-medium ${c.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {fmt(c.pnl)}
                    </td>
                    <td>
                      <Badge tone="run">running now</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {qChamps.data?.synced_until && (
          <div className="text-xs text-white/40 mt-3">
            Results synced through {fmtWhen(qChamps.data.synced_until)} ({certaintyLabel("last_known")}).
          </div>
        )}
      </Card>

      <Card
        title={`Graduated paper (${graduated.length})`}
        aside={`${evalLimit} closed trades, then ${"GRADUATED_PAPER"} or REJECTED_NEGATIVE_PNL`}
      >
        <div className="text-sm text-white/60 mb-3">
          Off the live book. <code>GRADUATED_PAPER</code> is positive paper P&L after the
          evaluation window — not authorization to trade real funds.
        </div>

        {!graduated.length ? (
          <Empty>
            No graduations recorded yet. Silence here is last-known empty state, not a
            graduation job in flight.
            {prog?.graduation.note ? ` ${prog.graduation.note}` : ""}
          </Empty>
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
                        {fmtWhen(g.graduated_at)} · {g.closed_trades} trades evaluated
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="text-right">
                        <div className={`text-base font-bold ${g.total_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {fmt(g.total_pnl)}
                        </div>
                        <div className="text-xs text-white/60">Win Rate: {g.win_rate_pct}%</div>
                      </div>
                      <Badge tone={g.status === "GRADUATED_PAPER" ? "pos" : "neg"}>{g.status}</Badge>
                      <button
                        onClick={() => setExpandedStrat(isExpanded ? null : g.name)}
                        className="px-3 py-1 text-xs bg-white/10 hover:bg-white/20 rounded text-white transition"
                      >
                        {isExpanded ? "Hide History" : `View ${evalLimit} Trades`}
                      </button>
                    </div>
                  </div>

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

      <Card
        title={`Discovery log (${discoveryLog.length} evaluations)`}
        aside={
          prog?.pipeline.stamp_says_in_progress && prog.pipeline.phase === "tournament"
            ? `stamp: tournament since ${fmtWhen(prog.pipeline.started_at)} — liveness not verified`
            : discoveryLog[0]?.tested_at
              ? `last tested ${fmtWhen(discoveryLog[0].tested_at)}`
              : "no live job signal"
        }
      >
        <div className="text-sm text-white/60 mb-3">
          Last-known backtest evaluations (5m history, combinatorial TA). This is not a
          live stream. If the API cannot see a running job, there is no spinner.
        </div>

        {!discoveryLog.length ? (
          <Empty>
            No discovery_log.json yet. Tournament writes it after a qualification batch.
            Empty means nothing has been recorded — not that discovery is running.
          </Empty>
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
