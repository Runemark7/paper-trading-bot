import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchChampions } from "../api/client";
import { Badge, Card, Empty } from "../components/ui";
import ChampionTape from "../chart/ChampionTape";
import { defaultChampionName } from "../chart/numberTrades";

export default function ChartPage() {
  const qChamps = useQuery({
    queryKey: ["champions"],
    queryFn: fetchChampions,
    refetchInterval: 30_000,
  });
  const champs = qChamps.data?.active_champions ?? [];
  const [champion, setChampion] = useState("");

  useEffect(() => {
    if (!champs.length) return;
    if (champion && champs.some((c) => c.name === champion)) return;
    setChampion(defaultChampionName(champs) ?? "");
  }, [champs, champion]);

  return (
    <div className="space-y-4 min-w-0">
      <div className="flex flex-wrap items-start gap-2">
        <Badge tone="run">Running now</Badge>
        <span className="text-sm text-white/60 min-w-0 break-words">
          One champion · 5m public Binance tape · refreshes every 20s
        </span>
      </div>

      <label className="block min-w-0">
        <span className="text-[11px] uppercase tracking-wider text-white/40">Champion</span>
        <select
          aria-label="Champion"
          value={champion}
          onChange={(e) => setChampion(e.target.value)}
          disabled={!champs.length}
          className="mt-1 w-full min-h-12 rounded-xl bg-[#121a38] px-3 text-base text-white ring-1 ring-white/15"
        >
          {!champs.length ? (
            <option value="">No active paper-book names</option>
          ) : (
            champs.map((c) => (
              <option key={c.name} value={c.name}>
                {c.name} · {c.open_lots ?? 0} open lots
              </option>
            ))
          )}
        </select>
      </label>

      <Card
        title={champion ? `${champion} · 5m` : "Champion chart"}
        aside={qChamps.data?.open_lots != null ? `${qChamps.data.open_lots} open lots on the book` : undefined}
      >
        {qChamps.isError ? (
          <Empty>Could not load /api/champions: {String(qChamps.error)}</Empty>
        ) : !champs.length ? (
          <Empty>
            No active champions to chart. The picker reads paper-book names from /api/champions.
          </Empty>
        ) : !champion ? (
          <Empty>Picking the champion with the most open lots…</Empty>
        ) : (
          <ChampionTape championName={champion} />
        )}
      </Card>
    </div>
  );
}
