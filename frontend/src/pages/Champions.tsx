import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchChampions, fetchGraduated, api } from "../api/client";
import { DiscoveryTeaser } from "../status/DiscoveryBuckets";
import {
  Badge,
  Card,
  Empty,
  Field,
  FieldGrid,
  MonoName,
  PhoneCards,
  DesktopTable,
  fmt,
} from "../components/ui";
import { FilterInput, SortTh } from "../components/tableControls";
import { ChampionSinceChip, PairLotChips } from "../status/ChampionLots";
import { LotHealthSummaryChips } from "../status/LotHealth";
import {
  applyChampionRows,
  CHAMPION_SORT_COLUMNS,
  championFiltersActive,
  championSortDirLabels,
  championSortSummary,
  cycleChampionSort,
  DEFAULT_CHAMPION_SORT,
  EMPTY_CHAMPION_FILTERS,
  initialChampionSortDir,
  isDefaultChampionSort,
  type ChampionColumnFilters,
  type ChampionListRow,
  type ChampionSort,
  type ChampionSortKey,
} from "../status/championFilters";
import { championDetailPath } from "../status/championPath";
import {
  accountsMatch,
  certaintyLabel,
  fmtChampionSince,
  fmtWhen,
  openLotsByPair,
  openLotsForChampion,
} from "../status/format";

const PAGE = 20;

export default function Champions() {
  const navigate = useNavigate();
  const qChamps = useQuery({ queryKey: ["champions"], queryFn: fetchChampions, refetchInterval: 30_000 });
  const qLive = useQuery({ queryKey: ["live"], queryFn: api.live, refetchInterval: 30_000 });
  const qGrad = useQuery({ queryKey: ["graduated"], queryFn: fetchGraduated, refetchInterval: 30_000 });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 15_000 });

  const [filters, setFilters] = useState<ChampionColumnFilters>(EMPTY_CHAMPION_FILTERS);
  const [sort, setSort] = useState<ChampionSort>(DEFAULT_CHAMPION_SORT);
  const [shown, setShown] = useState(PAGE);
  const [expandedStrat, setExpandedStrat] = useState<string | null>(null);

  const champs = qChamps.data?.active_champions ?? [];
  const evalLimit = qChamps.data?.evaluation_limit ?? 80;
  const graduated = qGrad.data ?? [];
  const run = status.data?.running_now;
  const prog = status.data?.in_progress;
  const rowLots = champs.reduce((n, c) => n + (c.open_lots ?? 0), 0);
  const openLots = qChamps.data?.open_lots ?? run?.open_lots ?? run?.positions_open ?? rowLots;
  const liveLotsKnown = qLive.data != null;
  const leftoverLots = Object.entries(qChamps.data?.open_lots_by_account ?? {}).filter(
    ([name, n]) => n > 0 && !champs.some((c) => accountsMatch(name, c.name)),
  );
  const filtersOn = championFiltersActive(filters);
  const sortOn = !isDefaultChampionSort(sort);

  const rows: ChampionListRow[] = useMemo(
    () =>
      champs.map((c) => {
        const split = openLotsByPair(qLive.data, c.name);
        const lots = openLotsForChampion(qLive.data, c.name);
        return {
          ...c,
          btc: liveLotsKnown ? split.btc : null,
          eth: liveLotsKnown ? split.eth : null,
          openLots: liveLotsKnown ? split.total : (c.open_lots ?? 0),
          liveLotsKnown,
          lots,
        };
      }),
    [champs, qLive.data, liveLotsKnown],
  );

  const viewed = useMemo(
    () => applyChampionRows(rows, filters, sort, fmtChampionSince, fmt),
    [rows, filters, sort],
  );
  const visible = viewed.slice(0, shown);

  function setColumn<K extends keyof ChampionColumnFilters>(key: K, value: ChampionColumnFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setShown(PAGE);
  }

  function applySort(next: ChampionSort) {
    setSort(next);
    setShown(PAGE);
  }

  function cycleColumn(key: ChampionSortKey) {
    applySort(cycleChampionSort(sort, key));
  }

  function openChampion(name: string) {
    navigate(championDetailPath(name));
  }

  return (
    <div className="space-y-6 min-w-0">
      <p className="text-sm text-white/55">
        Names on the paper book are isolated €10k accounts. Filter and sort the list
        like Discovery, then tap a row for that account&apos;s lots, closed trades, and
        BTC/ETH tape. Graduation below and Discovery (own page) are last-known
        pipeline results — not a live job unless the sidecar stamp says so.
      </p>

      <Card
        title={`On the paper book · ${openLots} open lots`}
        aside={run ? `${champs.length} champions · last cycle ${fmtWhen(run.cycle.last_cycle_at)}` : `${champs.length} champions`}
      >
        <div className="text-sm text-white/60 mb-3">
          Each name is an isolated paper account. Rows show that account&apos;s open
          lots split BTC vs ETH (same /api/live lots as Positions: two BTC + one ETH
          = BTC 2 · ETH 1, total 3). Tap a row for lots and the compact BTC/ETH
          chart. After <b>{evalLimit} closed entries</b> the account leaves this list.{" "}
          <code>GRADUATED_PAPER</code> is graduated paper, not live money.
          {run?.strategy.mode === "sma_stack_fallback" && (
            <> Pool empty — book is running <code>sma_stack</code> fallback.</>
          )}
        </div>

        {qChamps.error && champs.length ? (
          <div className="text-rose-400 text-sm mb-3">
            Could not refresh /api/champions: {String(qChamps.error)} — showing last-known.
          </div>
        ) : null}

        {champs.length ? (
          <div className="mb-3 rounded-lg border border-white/10 bg-white/[0.02] p-2 sm:p-3 space-y-2">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 min-w-0">
              <FilterInput
                label="Name"
                value={filters.name}
                onChange={(v) => setColumn("name", v)}
                placeholder="contains…"
              />
              <FilterInput
                label="Champion since"
                value={filters.since}
                onChange={(v) => setColumn("since", v)}
                placeholder="date…"
              />
              <FilterInput
                label="BTC lots"
                value={filters.btc}
                onChange={(v) => setColumn("btc", v)}
                placeholder=">=1"
              />
              <FilterInput
                label="ETH lots"
                value={filters.eth}
                onChange={(v) => setColumn("eth", v)}
                placeholder=">=1"
              />
              <FilterInput
                label="Open lots"
                value={filters.openLots}
                onChange={(v) => setColumn("openLots", v)}
                placeholder=">0"
              />
              <FilterInput
                label="Closed"
                value={filters.closed}
                onChange={(v) => setColumn("closed", v)}
                placeholder={`<${evalLimit}`}
              />
              <FilterInput
                label="Wins"
                value={filters.wins}
                onChange={(v) => setColumn("wins", v)}
                placeholder=">=0"
              />
              <FilterInput
                label="Paper P&L"
                value={filters.pnl}
                onChange={(v) => setColumn("pnl", v)}
                placeholder=">0"
              />
              <label className="min-w-0 block" htmlFor="champion-sort-key">
                <span className="text-[11px] uppercase tracking-wider text-white/40">Sort</span>
                <select
                  id="champion-sort-key"
                  value={sort.key}
                  onChange={(e) => {
                    const key = e.target.value as ChampionSortKey;
                    applySort({ key, dir: initialChampionSortDir(key) });
                  }}
                  aria-label="Sort Champions"
                  className="mt-1 w-full min-h-11 rounded-md bg-[#121a38] px-2 text-xs text-white ring-1 ring-white/15"
                >
                  {CHAMPION_SORT_COLUMNS.map((col) => (
                    <option key={col.key} value={col.key}>
                      {col.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-wider text-white/40 mb-1">Order</div>
                <button
                  type="button"
                  onClick={() => applySort({ key: sort.key, dir: sort.dir === "desc" ? "asc" : "desc" })}
                  aria-label="Toggle sort order"
                  className="min-h-11 w-full px-3 py-1.5 rounded text-xs bg-white/15 text-white hover:bg-white/20"
                >
                  {championSortDirLabels(sort.key)[sort.dir]} {sort.dir === "desc" ? "↓" : "↑"}
                </button>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-white/40">
                BTC, ETH, lots, closed, wins, and P&amp;L accept &gt;, &gt;=, &lt;, &lt;=, or =.
                Sort ranks the filtered list — highest / lowest on desktop headers and this
                Sort control.
              </p>
              <div className="flex flex-wrap gap-1">
                {sortOn ? (
                  <button
                    type="button"
                    onClick={() => applySort(DEFAULT_CHAMPION_SORT)}
                    className="min-h-11 px-3 py-1.5 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
                  >
                    Reset sort
                  </button>
                ) : null}
                {filtersOn ? (
                  <button
                    type="button"
                    onClick={() => {
                      setFilters(EMPTY_CHAMPION_FILTERS);
                      setShown(PAGE);
                    }}
                    className="min-h-11 px-3 py-1.5 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
                  >
                    Clear filters
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}

        <div className="flex flex-col gap-2 mb-2 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-xs uppercase tracking-wider text-white/40">Active champions</h3>
          <p className="text-xs text-white/40">
            {champs.length
              ? `Showing ${visible.length} of ${viewed.length}${filtersOn ? ` (filtered from ${champs.length})` : ""}${sortOn ? ` · ${championSortSummary(sort)}` : ""}`
              : null}
          </p>
        </div>

        {qChamps.error && !champs.length ? (
          <Empty>
            Could not load /api/champions: {String(qChamps.error)}
          </Empty>
        ) : !champs.length ? (
          <Empty>
            No champions in champions.json. The live book falls back to{" "}
            <code>{run?.strategy.fallback ?? "sma_stack"}</code> until discovery admits names.
          </Empty>
        ) : !viewed.length ? (
          <Empty>No champion rows match these column filters.</Empty>
        ) : (
          <>
            <PhoneCards>
              {visible.map((c) => (
                <li key={c.name} className="rounded-lg border border-white/10 min-w-0 overflow-hidden">
                  <Link
                    to={championDetailPath(c.name)}
                    className="block w-full text-left p-3 min-h-11 space-y-2 min-w-0 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-indigo-400"
                  >
                    <MonoName className="block w-full text-sm font-medium text-white">{c.name}</MonoName>
                    <div className="flex items-center justify-between gap-2 min-w-0">
                      <Badge tone="run">running now</Badge>
                      <span aria-hidden className="shrink-0 text-white/40 text-sm leading-none">
                        ▸
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1 min-w-0">
                      <PairLotChips
                        btc={c.liveLotsKnown && c.btc != null ? c.btc : "—"}
                        eth={c.liveLotsKnown && c.eth != null ? c.eth : "—"}
                      />
                      <LotHealthSummaryChips lots={c.lots} />
                      <ChampionSinceChip since={c.champion_since} />
                    </div>
                    <FieldGrid>
                      <Field label="Open lots">{c.openLots}</Field>
                      <Field label={`Closed (of ${evalLimit})`}>
                        {c.closed} / {evalLimit}
                      </Field>
                      <Field label="Wins">{c.wins}</Field>
                      <Field label="Paper P&L" className={c.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
                        <span className="font-medium">{fmt(c.pnl)}</span>
                      </Field>
                      <Field label="Champion since">{fmtChampionSince(c.champion_since)}</Field>
                    </FieldGrid>
                  </Link>
                </li>
              ))}
            </PhoneCards>
            <DesktopTable>
              <table className="w-full text-xs">
                <thead className="text-white/40 uppercase">
                  <tr>
                    <SortTh label="Name" column="name" sort={sort} onCycle={cycleColumn} />
                    <SortTh label="Since" column="since" sort={sort} onCycle={cycleColumn} />
                    <SortTh label="BTC" column="btc" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="ETH" column="eth" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="Open" column="openLots" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="Closed" column="closed" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="Wins" column="wins" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="Paper P&L" column="pnl" sort={sort} onCycle={cycleColumn} align="right" />
                  </tr>
                </thead>
                <tbody>
                  {visible.map((c) => (
                    <tr
                      key={c.name}
                      className="border-t border-white/5 cursor-pointer hover:bg-white/[0.04]"
                      onClick={() => openChampion(c.name)}
                    >
                      <td className="py-1" title={c.name}>
                        <Link
                          to={championDetailPath(c.name)}
                          className="block min-h-11 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-400"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <MonoName className="font-medium text-white">{c.name}</MonoName>
                        </Link>
                      </td>
                      <td className="text-white/50">{fmtChampionSince(c.champion_since)}</td>
                      <td className="text-right font-mono tabular-nums">
                        {c.liveLotsKnown && c.btc != null ? c.btc : "—"}
                      </td>
                      <td className="text-right font-mono tabular-nums">
                        {c.liveLotsKnown && c.eth != null ? c.eth : "—"}
                      </td>
                      <td className="text-right font-mono tabular-nums">{c.openLots}</td>
                      <td className="text-right font-mono tabular-nums">
                        {c.closed} / {evalLimit}
                      </td>
                      <td className="text-right font-mono tabular-nums">{c.wins}</td>
                      <td className={`text-right font-mono font-bold ${c.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {fmt(c.pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </DesktopTable>
            {viewed.length > shown && (
              <button
                type="button"
                onClick={() => setShown((n) => n + PAGE)}
                className="mt-3 min-h-11 px-3 py-2 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
              >
                Show more ({viewed.length - shown} left)
              </button>
            )}
          </>
        )}
        <div className="text-xs text-white/45 mt-3">
          {openLots} open lots on the paper book
          {rowLots !== openLots ? ` · ${rowLots} on listed champions` : " (rows sum to this total)"}.
        </div>
        {leftoverLots.length > 0 && (
          <div className="text-xs text-white/45 mt-1 break-all [overflow-wrap:anywhere]">
            Also {leftoverLots.reduce((n, [, lots]) => n + lots, 0)} open lots on accounts not in the pool:{" "}
            {leftoverLots.map(([name, lots]) => `${name} (${lots})`).join(", ")}.
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
          Off the live book. <code>GRADUATED_PAPER</code> is paper P&L greater than
          buy-and-hold of the same assets over the same period after the evaluation
          window — not authorization to trade real funds.
        </div>

        {!graduated.length ? (
          qGrad.error ? (
            <Empty>Could not load /api/graduated: {String(qGrad.error)}</Empty>
          ) : (
          <Empty>
            No graduations recorded yet. Silence here is last-known empty state, not a
            graduation job in flight.
            {prog?.graduation.note ? ` ${prog.graduation.note}` : ""}
          </Empty>
          )
        ) : (
          <div className="space-y-4">
            {graduated.map((g) => {
              const isExpanded = expandedStrat === g.name;
              return (
                <div key={g.name} className="border border-white/10 rounded-lg p-3 sm:p-4 bg-white/[0.02] min-w-0">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <MonoName className="block text-base font-semibold text-white">{g.name}</MonoName>
                      <div className="text-xs text-white/50 mt-1 break-words">
                        {fmtWhen(g.graduated_at)} · {g.closed_trades} trades evaluated
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="sm:text-right">
                        <div className={`text-base font-bold ${g.total_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {fmt(g.total_pnl)}
                        </div>
                        <div className="text-xs text-white/60">Win Rate: {g.win_rate_pct}%</div>
                      </div>
                      <Badge tone={g.status === "GRADUATED_PAPER" ? "pos" : "neg"}>{g.status}</Badge>
                      <button
                        type="button"
                        onClick={() => setExpandedStrat(isExpanded ? null : g.name)}
                        className="min-h-11 px-3 py-2 text-xs bg-white/10 hover:bg-white/20 rounded text-white transition"
                      >
                        {isExpanded ? "Hide History" : `View ${evalLimit} Trades`}
                      </button>
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="mt-4 pt-4 border-t border-white/10 min-w-0">
                      <div className="text-xs font-semibold uppercase text-white/40 mb-2">Detailed Trade History</div>
                      <PhoneCards>
                        {g.trade_history?.map((t, idx) => (
                          <li key={idx} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                            <div className="flex items-baseline justify-between gap-2 min-w-0">
                              <span className="font-medium min-w-0 truncate">{t.symbol}</span>
                              <span className={`shrink-0 font-bold ${t.pnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                                {fmt(t.pnl)}
                              </span>
                            </div>
                            <FieldGrid>
                              <Field label="Entry">{t.entry_ts?.replace("T", " ").slice(0, 16)}</Field>
                              <Field label="Entry $">${t.entry_price.toLocaleString()}</Field>
                              <Field label="Exit $">${t.exit_price.toLocaleString()}</Field>
                              <Field label="Qty">{t.size.toFixed(4)}</Field>
                              <Field label="Return">
                                <span className={t.pnl_pct >= 0 ? "text-emerald-400" : "text-rose-400"}>
                                  {(t.pnl_pct * 100).toFixed(2)}%
                                </span>
                              </Field>
                              <Field label="Exit reason">{t.exit_reason}</Field>
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
                      </DesktopTable>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>

      <DiscoveryTeaser />
    </div>
  );
}
