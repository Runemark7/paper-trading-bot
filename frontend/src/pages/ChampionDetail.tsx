import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, fetchChampions } from "../api/client";
import type { Champion, TradeRow } from "../api/types";
import ChampionTape from "../chart/ChampionTape";
import { closedTradesForChampion } from "../chart/numberTrades";
import {
  Badge,
  Card,
  CopyableName,
  DesktopTable,
  Empty,
  Field,
  FieldGrid,
  PhoneCards,
  fmt,
  fmtPct,
} from "../components/ui";
import {
  ChampionOpenLotsCard,
  ChampionSinceChip,
  PairLotChips,
} from "../status/ChampionLots";
import { decodeChampionName } from "../status/championPath";
import { LotHealthSummaryChips } from "../status/LotHealth";
import {
  accountsMatch,
  fmtChampionSince,
  fmtWhen,
  openLotsByPair,
  openLotsForChampion,
} from "../status/format";

function findChampion(champs: Champion[], name: string): Champion | undefined {
  return champs.find((c) => c.name === name) ?? champs.find((c) => accountsMatch(c.name, name));
}

function ClosedTradeHistory({ trades }: { trades: TradeRow[] }) {
  if (!trades.length) {
    return <Empty>No closed trades for this account in the /api/trades window.</Empty>;
  }
  return (
    <>
      <PhoneCards>
        {trades.map((t) => (
          <li
            key={`${t.account ?? ""}-${t.id}`}
            className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden"
          >
            <div className="flex items-baseline justify-between gap-2 min-w-0">
              <span className="font-medium min-w-0 truncate">{t.symbol ?? "—"}</span>
              <span className={`shrink-0 ${(t.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {t.pnl != null ? fmt(t.pnl) : fmtPct(t.pnl_pct)}
              </span>
            </div>
            <FieldGrid>
              <Field label="Condition" span mono>
                {t.condition}
              </Field>
              <Field label="Entry">{fmt(t.entry_price)}</Field>
              <Field label="Exit">{t.exit_price != null ? fmt(t.exit_price) : "—"}</Field>
              <Field label="Return">
                <span className={(t.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}>
                  {fmtPct(t.pnl_pct)}
                </span>
              </Field>
              <Field label="Exit reason" span>
                {t.exit_reason ?? "—"}
              </Field>
            </FieldGrid>
          </li>
        ))}
      </PhoneCards>
      <DesktopTable>
        <table className="w-full text-xs">
          <thead className="text-white/40 uppercase">
            <tr>
              <th className="text-left py-1">Symbol</th>
              <th className="text-left">Entry Time</th>
              <th className="text-right">Entry $</th>
              <th className="text-right">Exit $</th>
              <th className="text-right">P&L</th>
              <th className="text-right">Return</th>
              <th className="text-left">Exit Reason</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={`${t.account ?? ""}-${t.id}`} className="border-t border-white/5">
                <td className="py-1 font-medium">{t.symbol ?? "—"}</td>
                <td className="text-white/60">{t.entry_ts?.replace("T", " ").slice(0, 16)}</td>
                <td className="text-right font-mono">{fmt(t.entry_price)}</td>
                <td className="text-right font-mono">{t.exit_price != null ? fmt(t.exit_price) : "—"}</td>
                <td className={`text-right font-bold ${(t.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {t.pnl != null ? fmt(t.pnl) : "—"}
                </td>
                <td className={`text-right ${(t.pnl_pct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                  {fmtPct(t.pnl_pct)}
                </td>
                <td className="text-white/70">{t.exit_reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </DesktopTable>
    </>
  );
}

export default function ChampionDetail() {
  const params = useParams();
  const name = decodeChampionName(params["*"] ?? params.name);

  const qChamps = useQuery({ queryKey: ["champions"], queryFn: fetchChampions, refetchInterval: 30_000 });
  const qLive = useQuery({ queryKey: ["live"], queryFn: api.live, refetchInterval: 30_000 });
  const qTrades = useQuery({
    queryKey: ["trades", "champion-detail"],
    queryFn: () => api.trades(undefined, 200),
    refetchInterval: 30_000,
    enabled: Boolean(name),
  });

  const champs = (qChamps.data?.active_champions ?? []).filter(
    (c): c is Champion => typeof c.name === "string" && c.name.length > 0,
  );
  const evalLimit = qChamps.data?.evaluation_limit ?? 80;
  const champ = findChampion(champs, name);
  const liveReady = qLive.data !== undefined || qLive.isError;
  const liveLotsKnown = qLive.data != null;
  const split = openLotsByPair(qLive.data, name);
  const lots = openLotsForChampion(qLive.data, name);
  const closed = closedTradesForChampion(qTrades.data, name);
  const knownCount = champ?.open_lots ?? 0;

  return (
    <div className="space-y-4 min-w-0">
      <Link
        to="/champions"
        className="inline-flex min-h-11 items-center px-3 py-2 text-sm bg-white/10 hover:bg-white/20 rounded text-white"
      >
        ← Champions
      </Link>

      {!name ? (
        <Empty>Missing champion name in the URL.</Empty>
      ) : qChamps.isError && !champs.length ? (
        <Empty>Could not load /api/champions: {String(qChamps.error)}</Empty>
      ) : (
        <>
          <Card
            title="Champion"
            aside={champ ? "on the paper book" : "not in the current pool"}
          >
            {qChamps.error && champs.length ? (
              <p className="text-xs text-rose-400 mb-2 min-w-0 break-words">
                Could not refresh /api/champions: {String(qChamps.error)} — showing last-known.
              </p>
            ) : null}
            <div className="space-y-3 min-w-0">
              <div>
                <div className="text-[11px] uppercase tracking-wider text-white/40">Strategy</div>
                <CopyableName name={name} className="mt-0.5" />
              </div>
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                {champ ? <Badge tone="run">running now</Badge> : <Badge tone="wait">not on the book</Badge>}
                <PairLotChips
                  btc={liveLotsKnown ? split.btc : "—"}
                  eth={liveLotsKnown ? split.eth : "—"}
                />
                <LotHealthSummaryChips lots={lots} />
                {champ ? <ChampionSinceChip since={champ.champion_since} /> : null}
              </div>
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 min-w-0 text-xs text-white/50">
                <span className="min-w-0 break-words [overflow-wrap:anywhere]">
                  Started {fmtChampionSince(champ?.champion_since)}
                </span>
              </div>
              {champ ? (
                <FieldGrid>
                  <Field label="Open lots">{liveLotsKnown ? split.total : knownCount}</Field>
                  <Field label={`Closed (of ${evalLimit})`}>
                    {champ.closed} / {evalLimit}
                  </Field>
                  <Field label="Wins">{champ.wins}</Field>
                  <Field label="Paper P&L" className={(champ.pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}>
                    <span className="font-medium">{fmt(champ.pnl)}</span>
                  </Field>
                  <Field label="Champion since">{fmtChampionSince(champ.champion_since)}</Field>
                </FieldGrid>
              ) : (
                <p className="text-sm text-white/55">
                  This name is not in the current champions.json pool. Open lots below still
                  join /api/live the same way as Positions (name · slug · trades_*).
                </p>
              )}
            </div>
          </Card>

          <ChampionOpenLotsCard lots={lots} liveReady={liveReady} knownCount={knownCount} />

          <Card
            title={`Closed trades · ${closed.length}`}
            aside="same /api/trades window as Positions and the tape"
          >
            {qTrades.isError && !qTrades.data?.length ? (
              <Empty>Could not load /api/trades: {String(qTrades.error)}</Empty>
            ) : (
              <ClosedTradeHistory trades={closed} />
            )}
          </Card>

          <Card
            title="5m tape"
            aside={
              liveLotsKnown
                ? `BTC ${split.btc} · ETH ${split.eth} · ${split.total} lots`
                : `${knownCount} open lots on this account`
            }
          >
            <p className="text-xs text-white/45 mb-3 min-w-0 break-words">
              Same ChampionTape as Chart, scoped to this account only.
            </p>
            <ChampionTape championName={name} />
          </Card>

          {qChamps.data?.synced_until ? (
            <p className="text-xs text-white/40">
              Results synced through {fmtWhen(qChamps.data.synced_until)}.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
}
