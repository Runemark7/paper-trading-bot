import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, fetchChampionTrades, fetchChampions } from "../api/client";
import { EXIT_RULES, SIZING_RULES } from "../lib/rules";
import { Disclosure, EmptyState, RuleList, SkeletonRows, toneOf, usd } from "../components/kit";
import TradeLog from "../components/TradeLog";
import { forwardPnl, summaryFor } from "../status/ChampionBook";
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
import { AdmissionBadge } from "../status/admission";
import { ForwardStatusBadge, forwardStatusText } from "../status/forwardStatus";

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

  const qLog = useQuery({
    queryKey: ["champion-trades", "one", name],
    queryFn: () => fetchChampionTrades({ name, limit: 500 }),
    refetchInterval: 60_000,
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
  const book = summaryFor(qLog.data, name);
  const bookPnl = forwardPnl(book);

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
                {champ ? <AdmissionBadge admission={champ.admission} /> : null}
                {champ ? <ForwardStatusBadge status={champ.forward_status} /> : null}
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
                  <Field label="Forward test">{forwardStatusText(champ.forward_status)}</Field>
                </FieldGrid>
              ) : (
                <p className="text-sm text-white/55">
                  This name is not in the current champions.json pool. Open lots below still
                  join /api/live the same way as Positions (name · slug · trades_*).
                </p>
              )}
            </div>
            {book ? (
              <div className="mt-4 grid grid-cols-2 gap-3 rounded-xl bg-white/[0.03] p-3 sm:grid-cols-4">
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-white/40">Equity</div>
                  <div className="text-sm font-semibold tabular-nums">{usd(book.equity ?? book.start_cash, 0)}</div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-white/40">P&amp;L after fees</div>
                  <div className={`text-sm font-semibold tabular-nums ${toneOf(bookPnl) === "neg" ? "text-rose-300" : toneOf(bookPnl) === "pos" ? "text-emerald-300" : ""}`}>
                    {usd(bookPnl, 2, true)}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-white/40">Fees paid</div>
                  <div className="text-sm font-semibold tabular-nums">{usd(book.fees_paid)}</div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-white/40">Trades</div>
                  <div className="text-sm font-semibold tabular-nums">{book.closed} closed · {book.open} open</div>
                </div>
              </div>
            ) : null}
          </Card>

          <Card title="Rules in plain words" aside="entry blocks from the name · exits are the same for every champion">
            <div className="grid gap-4 md:grid-cols-2 min-w-0">
              <div className="min-w-0">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/45">Buys when all are true</div>
                <RuleList name={name} />
              </div>
              <div className="min-w-0">
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/45">Exits and sizing</div>
                <ul className="space-y-1 text-sm text-white/70">
                  {[...EXIT_RULES, ...SIZING_RULES].map((r) => (
                    <li key={r} className="flex gap-2">
                      <span aria-hidden className="text-white/30">•</span>
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Card>

          <Card title="Trade log" aside="every buy and sell · P&L after both fees">
            {qLog.isLoading ? (
              <SkeletonRows rows={3} />
            ) : qLog.data?.events.length ? (
              <TradeLog events={qLog.data.events} />
            ) : qLog.isError ? (
              <p className="text-sm text-rose-300">Could not load the trade log: {String(qLog.error)}</p>
            ) : (
              <EmptyState title="No trades yet" icon="◷">
                This champion buys only when every entry rule above is true on the same closed candle. Until that happens
                the account stays flat in cash — waiting, not broken.
              </EmptyState>
            )}
          </Card>

          <ChampionOpenLotsCard lots={lots} liveReady={liveReady} knownCount={knownCount} />

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

          <Disclosure
            title={`Closed trades with entry condition · ${closed.length}`}
            summary="Same /api/trades window as Positions and the tape"
          >
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

          </Disclosure>

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
