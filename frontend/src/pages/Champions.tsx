import { useQuery } from "@tanstack/react-query";
import { fetchChampions } from "../api/client";
import { Card, fmt, Badge } from "../components/ui";

export default function Champions() {
  const q = useQuery({ queryKey: ["champions"], queryFn: fetchChampions, refetchInterval: 60_000 });

  const champs = q.data?.champions ?? [];
  const max = q.data?.max ?? 64;

  return (
    <div className="space-y-4">
      <div className="text-sm text-white/60">
        Live-test champion pool: <b>{champs.length}</b> / {max}. Champions with ≥10 closed
        trades are marked passed (the "wait for new evaluations" threshold).
      </div>

      {q.error && <div className="text-rose-400 text-sm">Error: {String(q.error)}</div>}

      {!champs.length ? (
        <div className="text-white/40 text-sm">No champions in the live pool yet — closed paper trades will populate it.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-white/40 text-xs uppercase">
              <tr>
                <th className="text-left py-2">Strategy</th>
                <th className="text-right">Closed</th>
                <th className="text-right">Wins</th>
                <th className="text-right">P&L</th>
                <th className="text-left">Status</th>
              </tr>
            </thead>
            <tbody>
              {champs.map((c) => (
                <tr key={c.name} className="border-t border-white/5">
                  <td className="py-2 font-mono text-sm">{c.name}</td>
                  <td className="text-right">{c.closed}</td>
                  <td className="text-right">{c.wins}</td>
                  <td className={`text-right ${c.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {fmt(c.pnl)}
                  </td>
                  <td>
                    {c.passed ? <Badge tone="pos">passed</Badge> : <Badge tone="neutral">testing</Badge>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}