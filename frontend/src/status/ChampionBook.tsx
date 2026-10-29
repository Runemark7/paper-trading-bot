import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { Champion, ChampionTradeSummary, ChampionTradesPayload } from "../api/types";
import { EXIT_RULES, SIZING_RULES } from "../lib/rules";
import {
  EmptyState,
  ErrorNote,
  InfoTip,
  Kpi,
  Panel,
  RuleList,
  SectionTitle,
  SkeletonRows,
  shortAgo,
  toneOf,
  usd,
  utcStamp,
} from "../components/kit";
import TradeLog from "../components/TradeLog";
import { MonoName } from "../components/ui";
import { AdmissionBadge } from "./admission";
import { isLibraryName, LibraryBacktestBox } from "./LibraryBacktest";
import { ForwardStatusBadge, forwardStatusText } from "./forwardStatus";
import { championDetailPath } from "./championPath";
import { accountsMatch, fmtChampionSince } from "./format";

/** Forward P&L for one account: marked equity minus start cash when a snapshot exists, else realized. */
export function forwardPnl(s: ChampionTradeSummary | undefined): number | null {
  if (!s) return null;
  if (s.equity != null) return s.equity - s.start_cash;
  return s.realized_pnl;
}

export function summaryFor(payload: ChampionTradesPayload | undefined, name: string): ChampionTradeSummary | undefined {
  return payload?.champions.find((c) => accountsMatch(c.name, name));
}

export function BookKpis({
  champs,
  trades,
  loading,
}: {
  champs: Champion[];
  trades: ChampionTradesPayload | undefined;
  loading: boolean;
}) {
  const active = (trades?.champions ?? []).filter((c) => champs.some((x) => accountsMatch(x.name, c.name)));
  const pnl = active.reduce((n, c) => n + (forwardPnl(c) ?? 0), 0);
  const closed = active.reduce((n, c) => n + c.closed, 0);
  const wins = active.reduce((n, c) => n + c.wins, 0);
  const fees = active.reduce((n, c) => n + c.fees_paid, 0);
  const open = active.reduce((n, c) => n + c.open, 0);
  return (
    <div className="grid grid-cols-2 gap-2 sm:gap-3 lg:grid-cols-5">
      <Kpi label="Active champions" value={String(champs.length)} hint="admitted by the owner" loading={loading} />
      <Kpi
        label="Forward P&L"
        value={usd(pnl, 2, true)}
        tone={toneOf(pnl)}
        hint="after fees, all champions"
        loading={loading || !trades}
        info={
          <InfoTip label="Forward P&L">
            Marked equity minus starting cash for every active champion account, so it includes open positions at the latest
            price. Every trade pays a 0.1% taker fee on entry and exit plus slippage.
          </InfoTip>
        }
      />
      <Kpi label="Closed trades" value={closed} hint={closed ? `${Math.round((wins / closed) * 100)}% winners` : "none yet"} loading={loading || !trades} />
      <Kpi label="Fees paid" value={usd(fees)} hint="already inside P&L" loading={loading || !trades} />
      <Kpi label="Open positions" value={open} hint={open ? "waiting for stop or target" : "all flat"} loading={loading || !trades} />
    </div>
  );
}

function Stat({ label, children, tone }: { label: string; children: React.ReactNode; tone?: "pos" | "neg" | "neutral" }) {
  const c = tone === "pos" ? "text-emerald-300" : tone === "neg" ? "text-rose-300" : "text-white";
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wider text-white/40">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${c}`}>{children}</div>
    </div>
  );
}

function NoTradesYet({ since }: { since?: string | null }) {
  return (
    <EmptyState title="No trades yet" icon="◷">
      This champion only buys when all of its entry rules are true on a closed candle at the same time
      {since ? ` and it has been live since ${fmtChampionSince(since)}` : ""}. Strict combinations can stay flat for days — that
      is the strategy waiting, not a fault.
    </EmptyState>
  );
}

export function ChampionCard({
  champ,
  summary,
  events,
  evalLimit,
  tradesLoading,
  index,
}: {
  index: number;
  champ: Champion;
  summary: ChampionTradeSummary | undefined;
  events: ChampionTradesPayload["events"];
  evalLimit: number;
  tradesLoading: boolean;
}) {
  const pnl = forwardPnl(summary);
  const mine = events.filter((e) => accountsMatch(e.champion, champ.name)).slice(0, 4);
  const openEntries = summary?.open_entries ?? [];
  const closed = summary?.closed ?? champ.closed;
  const wins = summary?.wins ?? champ.wins;
  return (
    <Panel className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between min-w-0">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-semibold text-white">Champion {index + 1}</span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-200 ring-1 ring-emerald-400/25">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
              {openEntries.length ? "In a position" : "Flat — waiting for entry"}
            </span>
            <AdmissionBadge admission={champ.admission} />
            <ForwardStatusBadge status={champ.forward_status} />
          </div>
          <MonoName className="mt-2 block text-sm text-white/90">{champ.name}</MonoName>
          <div className="mt-1 text-xs text-white/45">
            Champion since {fmtChampionSince(champ.champion_since)}
            {champ.forward_status ? ` · ${forwardStatusText(champ.forward_status)}` : ""}
          </div>
        </div>
        <Link
          to={championDetailPath(champ.name)}
          className="inline-flex min-h-11 shrink-0 items-center rounded-lg bg-white/10 px-3 text-sm text-white hover:bg-white/15"
        >
          Chart &amp; details →
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-2 min-w-0">
        <div className="min-w-0">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/45">Buys when all are true</div>
          <RuleList name={champ.name} />
        </div>
        <div className="min-w-0">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/45">Then manages the trade</div>
          <ul className="space-y-1 text-sm text-white/70">
            {[...EXIT_RULES, SIZING_RULES[0]].map((r) => (
              <li key={r} className="flex gap-2">
                <span aria-hidden className="text-white/30">•</span>
                {r}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/45">Paper P&amp;L · live account</div>
        <div className="grid grid-cols-2 gap-3 rounded-xl bg-white/[0.03] p-3 sm:grid-cols-5">
        <Stat label="Equity">{summary?.equity != null ? usd(summary.equity, 0) : usd(summary?.start_cash ?? null, 0)}</Stat>
        <Stat label="P&L after fees" tone={toneOf(pnl)}>
          {pnl != null ? usd(pnl, 2, true) : usd(champ.pnl, 2, true)}
        </Stat>
        <Stat label="Realized">{usd(summary?.realized_pnl ?? champ.pnl, 2, true)}</Stat>
        <Stat label="Fees paid">{usd(summary?.fees_paid ?? null)}</Stat>
        <Stat label={`Closed / ${evalLimit}`}>
          {closed}
          {closed ? <span className="ml-1 text-xs font-normal text-white/45">({Math.round((wins / closed) * 100)}% won)</span> : null}
        </Stat>
        </div>
      </div>

      {isLibraryName(champ.name) ? <LibraryBacktestBox result={champ.library_result} /> : null}

      {openEntries.length ? (
        <div className="rounded-xl border border-indigo-400/20 bg-indigo-500/[0.06] p-3">
          <div className="text-xs font-semibold uppercase tracking-wider text-indigo-200/80">Open position</div>
          <ul className="mt-1 space-y-1 text-sm text-white/80">
            {openEntries.map((e) => (
              <li key={`${e.trade_id}`} className="flex flex-wrap gap-x-3">
                <span>
                  {e.size?.toFixed(5)} {e.symbol?.split("/")[0] ?? "BTC"} @ {usd(e.price)}
                </span>
                <span className="text-white/45">entered {shortAgo(e.ts)} · {usd(e.notional)} · fee {usd(e.fee)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="min-w-0">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-white/45">Latest trades</div>
        {tradesLoading ? <SkeletonRows rows={2} /> : mine.length ? <TradeLog events={mine} /> : <NoTradesYet since={champ.champion_since} />}
      </div>
    </Panel>
  );
}

export function CombinedTradeLog({
  champs,
  trades,
  loading,
  error,
}: {
  champs: Champion[];
  trades: ChampionTradesPayload | undefined;
  loading: boolean;
  error: unknown;
}) {
  const [who, setWho] = useState<string>("");
  const events = useMemo(
    () => (trades?.events ?? []).filter((e) => !who || accountsMatch(e.champion, who)),
    [trades, who],
  );
  return (
    <Panel>
      <SectionTitle
        title="Trade log — every champion"
        aside={trades ? `as of ${utcStamp(trades.as_of)}` : null}
      >
        Every buy and sell, newest first. P&amp;L is after both entry and exit fees.
      </SectionTitle>
      {champs.length > 1 ? (
        <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Filter by champion">
          {[{ key: "", label: "All champions" }, ...champs.map((c, i) => ({ key: c.name, label: `Champion ${i + 1}` }))].map((o) => (
            <button
              key={o.key || "all"}
              type="button"
              aria-pressed={who === o.key}
              title={o.key || undefined}
              onClick={() => setWho(o.key)}
              className={`min-h-9 rounded-full px-3 text-xs ring-1 ${
                who === o.key ? "bg-indigo-500/25 text-white ring-indigo-400/40" : "bg-white/5 text-white/60 ring-white/10 hover:bg-white/10"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      ) : null}
      {error ? <div className="mb-3"><ErrorNote what="the trade log" error={error} stale={!!trades} /></div> : null}
      {loading && !trades ? (
        <SkeletonRows rows={4} />
      ) : events.length ? (
        <TradeLog events={events} showChampion={!who} />
      ) : (
        <EmptyState title="No trades recorded yet" icon="◷">
          Champions only trade when every entry rule lines up on a closed candle. A fresh champion can be flat for days.
        </EmptyState>
      )}
      {trades?.champions.some((c) => c.truncated) ? (
        <p className="mt-2 text-xs text-white/40">Showing the latest 500 trades per champion.</p>
      ) : null}
    </Panel>
  );
}
