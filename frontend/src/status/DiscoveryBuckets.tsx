import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchDiscoverySummary } from "../api/client";
import type { DiscoveryEvaluation, DiscoverySummary } from "../api/types";
import { FilterInput, SortTh } from "../components/tableControls";
import { Badge, Card, Empty, Field, FieldGrid, MonoName, NameChip, PhoneCards, DesktopTable, fmt } from "../components/ui";
import { farmStatusLabel } from "./FarmControl";
import { fmtWhen } from "./format";
import {
  DEFAULT_TESTED_SORT,
  EMPTY_TESTED_FILTERS,
  TESTED_SORT_COLUMNS,
  applyTestedRows,
  cycleTestedSort,
  initialSortDir,
  isDefaultTestedSort,
  sortDirLabels,
  testedFiltersActive,
  testedSortSummary,
  type ResultFilter,
  type TestedColumnFilters,
  type TestedSort,
  type TestedSortKey,
} from "./discoveryFilters";

const PAGE = 20;

function testedTone(ok: boolean): "pos" | "neg" {
  return ok ? "pos" : "neg";
}

function windowDiagnostic(d: DiscoveryEvaluation): string | null {
  if (d.all_windows_nonneg === false) {
    return "empty/neg window (diagnostic, not a veto)";
  }
  return null;
}

function TestedRowFields({ d }: { d: DiscoveryEvaluation }) {
  return (
    <FieldGrid>
      <Field label="Tested">{fmtWhen(d.tested_at)}</Field>
      <Field label="OOS trades">{d.trades ?? "—"}</Field>
      <Field label="Sharpe">{d.sharpe != null ? d.sharpe.toFixed(2) : "—"}</Field>
      <Field label="Test P&L">
        <span className={`font-bold ${(d.test_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
          {fmt(d.test_pnl)}
        </span>
      </Field>
    </FieldGrid>
  );
}

/** Short Champions-page pointer — full buckets live on /discovery. */
export function DiscoveryTeaser() {
  const q = useQuery<DiscoverySummary, Error>({
    queryKey: ["discovery-summary", "compact"],
    queryFn: () => fetchDiscoverySummary({ compact: true }),
    refetchInterval: 15_000,
  });
  const counts = q.data?.counts;
  const uniqueTested = counts?.unique_tested ?? counts?.tested;
  const stuck = Boolean(q.data?.stuck || q.data?.in_flight?.stale);

  return (
    <Card title="Discovery" aside="full buckets on /discovery">
      <p className="text-sm text-white/60 mb-3">
        Last-known tested / in-flight / leftover buckets. The full list,
        already-tested column filters, and highest/lowest sort live on the
        Discovery page — not a live job.
      </p>
      {q.isError && (
        <div className="text-rose-300 text-sm mb-3">
          Could not load /api/discovery/summary: {String(q.error)}
        </div>
      )}
      {stuck && (
        <div className="mb-3 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          {q.data?.stuck_reason || q.data?.in_flight?.note || "discovery stuck / cycle overdue"}
        </div>
      )}
      {counts ? (
        <div className="flex flex-wrap gap-2 text-xs text-white/50 mb-3">
          <Badge tone="pos">qualified {counts.tested_pass}</Badge>
          <Badge tone="neg">rejected {counts.tested_fail}</Badge>
          <Badge tone="wait">not tested {counts.untested}</Badge>
          <Badge tone="neutral">unique {uniqueTested ?? "…"}</Badge>
        </div>
      ) : null}
      <p className="text-xs text-white/45 mb-3">
        Farm: {farmStatusLabel(q.data?.farm)}
        {q.data?.farm?.heartbeat_at ? ` · heartbeat ${fmtWhen(q.data.farm.heartbeat_at)}` : ""}
        {" · "}
        last eval {q.data?.last_tested_at ? fmtWhen(q.data.last_tested_at) : "never recorded"}
        {q.data?.last_strategy ? (
          <>
            {" "}
            · <MonoName className="text-xs">{q.data.last_strategy}</MonoName>
          </>
        ) : null}
      </p>
      <Link
        to="/discovery"
        className="inline-flex min-h-11 items-center px-3 py-2 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
      >
        Open Discovery
      </Link>
    </Card>
  );
}

export default function DiscoveryBuckets() {
  const q = useQuery<DiscoverySummary, Error>({
    queryKey: ["discovery-summary"],
    queryFn: () => fetchDiscoverySummary(),
    refetchInterval: 15_000,
  });
  const [filters, setFilters] = useState<TestedColumnFilters>(EMPTY_TESTED_FILTERS);
  const [sort, setSort] = useState<TestedSort>(DEFAULT_TESTED_SORT);
  const [shown, setShown] = useState(PAGE);
  const [shownQueued, setShownQueued] = useState(PAGE);

  const data = q.data;
  const tested = data?.tested ?? [];
  const viewed = useMemo(
    () => applyTestedRows(tested, filters, sort, fmtWhen, fmt),
    [tested, filters, sort],
  );
  const visible = viewed.slice(0, shown);
  const flight = data?.in_flight;
  const queued = data?.queued ?? data?.untested ?? [];
  const counts = data?.counts;
  const uniqueTested = counts?.unique_tested ?? counts?.tested;
  const logRows = counts?.log_rows;
  const stuck = Boolean(data?.stuck || flight?.stale);
  const filtersOn = testedFiltersActive(filters);
  const sortOn = !isDefaultTestedSort(sort);

  function setColumn<K extends keyof TestedColumnFilters>(key: K, value: TestedColumnFilters[K]) {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setShown(PAGE);
  }

  function applySort(next: TestedSort) {
    setSort(next);
    setShown(PAGE);
  }

  function cycleColumn(key: TestedSortKey) {
    applySort(cycleTestedSort(sort, key));
  }

  return (
    <Card
      title="Discovery"
      aside={
        counts
          ? `${uniqueTested ?? 0} unique tested · ${counts.tested_pass} qualified · ${counts.tested_fail} rejected · ${counts.untested} not tested`
          : "last-known buckets"
      }
    >
      <p className="text-sm text-white/60 mb-4">
        Last-known backtest evaluations (5m history, same tape as live). Not a live
        job. Walk-forwards run on the Windows discovery worker, not the k8s cycle
        sidecar. Champions and graduated names stay on the Champions page — they are
        not leftover untested. Stamp in-flight is not process liveness. Unique
        tested is latest-eval-per-name, not how many log rows were ever written.
        Already tested · rejected is parked forever — fail once, never retested.
      </p>

      {q.isError && (
        <div className="text-rose-300 text-sm mb-3">
          Could not load /api/discovery/summary: {String(q.error)}
        </div>
      )}

      {stuck && (
        <div className="mb-4 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          {data?.stuck_reason || flight?.note || "discovery stuck / cycle overdue"}
        </div>
      )}

      <div className="flex flex-wrap gap-2 text-xs text-white/50 mb-4">
        <Badge tone="neutral">universe {counts?.universe ?? "…"}</Badge>
        <Badge tone="neutral">champions {counts?.champions ?? "…"}</Badge>
        <Badge tone="neutral">graduated {counts?.graduated ?? "…"}</Badge>
        <Badge tone="pos">qualified {counts?.tested_pass ?? "…"}</Badge>
        <Badge tone="neg">rejected {counts?.tested_fail ?? "…"}</Badge>
        <Badge tone="wait">not tested {counts?.untested ?? "…"}</Badge>
        <Badge tone="neutral">
          unique {uniqueTested ?? "…"}
          {logRows != null ? ` · ${logRows} log rows` : ""}
        </Badge>
        <Badge tone="neutral">evals today {counts?.evals_today ?? "…"}</Badge>
        {(counts?.rejected_parked ?? counts?.tested_fail ?? 0) > 0 ? (
          <Badge tone="neg">parked forever {counts?.rejected_parked ?? counts?.tested_fail}</Badge>
        ) : null}
      </div>

      <section className="mb-5 min-w-0">
        <h3 className="text-xs uppercase tracking-wider text-white/40 mb-2">Being tested</h3>
        {stuck && !flight?.active ? (
          <p className="text-sm text-amber-100">
            {data?.stuck_reason || "discovery stuck / cycle overdue"}
            {data?.last_tested_at ? (
              <>
                {" "}
                · last eval {fmtWhen(data.last_tested_at)}
                {data.last_strategy ? (
                  <>
                    {" "}
                    · <MonoName className="text-xs">{data.last_strategy}</MonoName>
                  </>
                ) : null}
              </>
            ) : null}
          </p>
        ) : flight?.active ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={flight.stale || stuck ? "warn" : "wait"}>
                {flight.stale || stuck ? "discovery stuck / cycle overdue" : "stamp: tournament"}
              </Badge>
              <span className="text-xs text-white/50">
                since {fmtWhen(flight.stamp_started_at ?? flight.started_at)} · liveness not verified
              </span>
            </div>
            {flight.current ? (
              <p className="text-xs text-white/60">
                current <MonoName className="text-xs">{flight.current}</MonoName>
                {flight.remaining?.length ? ` · ${flight.remaining.length} remaining this cycle` : ""}
              </p>
            ) : null}
            {(flight.names ?? []).length ? (
              <div className="flex flex-wrap gap-1.5 min-w-0">
                {(flight.names ?? []).map((name) => (
                  <NameChip key={name} name={name} className="bg-amber-500/15 text-amber-100" />
                ))}
              </div>
            ) : (
              <p className="text-sm text-white/55">
                Tournament since {fmtWhen(flight.stamp_started_at ?? flight.started_at)}
                {flight.batch_size != null ? ` · batch ${flight.batch_size}` : ""}. Name list
                not persisted — not inventing names.
              </p>
            )}
            <p className="text-xs text-white/40">{flight.note}</p>
          </div>
        ) : (
          <p className="text-sm text-white/55">
            idle — last eval {data?.last_tested_at ? fmtWhen(data.last_tested_at) : "never recorded"}
            {data?.last_strategy ? (
              <>
                {" "}
                · <MonoName className="text-xs">{data.last_strategy}</MonoName>
              </>
            ) : null}
          </p>
        )}
      </section>

      <section className="mb-5 min-w-0">
        <div className="flex flex-col gap-2 mb-2 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-xs uppercase tracking-wider text-white/40">Already tested</h3>
          <p className="text-xs text-white/40">
            {tested.length
              ? `Showing ${visible.length} of ${viewed.length}${filtersOn ? ` (filtered from ${tested.length})` : ""}${sortOn ? ` · ${testedSortSummary(sort)}` : ""}`
              : null}
          </p>
        </div>
        <p className="text-xs text-white/45 mb-2">
          Already tested · rejected means parked forever (fail once). Those names
          are not retested and do not return after a cooldown.
        </p>

        {tested.length ? (
          <div className="mb-3 rounded-lg border border-white/10 bg-white/[0.02] p-2 sm:p-3 space-y-2">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 min-w-0">
              <FilterInput
                label="Name"
                value={filters.strategy}
                onChange={(v) => setColumn("strategy", v)}
                placeholder="contains…"
              />
              <FilterInput
                label="When"
                value={filters.testedAt}
                onChange={(v) => setColumn("testedAt", v)}
                placeholder="date or relative…"
              />
              <div className="min-w-0 col-span-2 md:col-span-1">
                <div className="text-[11px] uppercase tracking-wider text-white/40 mb-1">Result</div>
                <div className="flex flex-wrap gap-1" role="group" aria-label="Filter Result">
                  {(["all", "qualified", "rejected"] as ResultFilter[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setColumn("result", key)}
                      className={`min-h-11 px-3 py-1.5 rounded text-xs capitalize ${
                        filters.result === key ? "bg-white/15 text-white" : "bg-white/5 text-white/60 hover:bg-white/10"
                      }`}
                    >
                      {key}
                      {key === "all"
                        ? ` (${tested.length})`
                        : key === "qualified"
                          ? ` (${counts?.tested_pass ?? 0})`
                          : ` (${counts?.tested_fail ?? 0})`}
                    </button>
                  ))}
                </div>
              </div>
              <FilterInput
                label="Sharpe"
                value={filters.sharpe}
                onChange={(v) => setColumn("sharpe", v)}
                placeholder=">1.0 or 0.8"
              />
              <FilterInput
                label="OOS trades"
                value={filters.trades}
                onChange={(v) => setColumn("trades", v)}
                placeholder=">=30"
              />
              <FilterInput
                label="Test P&L"
                value={filters.testPnl}
                onChange={(v) => setColumn("testPnl", v)}
                placeholder="<0"
              />
              <FilterInput
                label="Fail reasons"
                value={filters.failReasons}
                onChange={(v) => setColumn("failReasons", v)}
                placeholder="contains…"
              />
              <label className="min-w-0 block" htmlFor="tested-sort-key">
                <span className="text-[11px] uppercase tracking-wider text-white/40">Sort</span>
                <select
                  id="tested-sort-key"
                  value={sort.key}
                  onChange={(e) => {
                    const key = e.target.value as TestedSortKey;
                    applySort({ key, dir: initialSortDir(key) });
                  }}
                  aria-label="Sort Already tested"
                  className="mt-1 w-full min-h-11 rounded-md bg-[#121a38] px-2 text-xs text-white ring-1 ring-white/15"
                >
                  {TESTED_SORT_COLUMNS.map((col) => (
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
                  {sortDirLabels(sort.key)[sort.dir]} {sort.dir === "desc" ? "↓" : "↑"}
                </button>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-[11px] text-white/40">
                Sharpe, trades, and P&amp;L accept &gt;, &gt;=, &lt;, &lt;=, or =. When matches the
                UTC stamp or relative time. Sort ranks the filtered list — highest / lowest on
                desktop headers and this Sort control.
              </p>
              <div className="flex flex-wrap gap-1">
                {sortOn ? (
                  <button
                    type="button"
                    onClick={() => applySort(DEFAULT_TESTED_SORT)}
                    className="min-h-11 px-3 py-1.5 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
                  >
                    Reset sort
                  </button>
                ) : null}
                {filtersOn ? (
                  <button
                    type="button"
                    onClick={() => {
                      setFilters(EMPTY_TESTED_FILTERS);
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

        {!tested.length ? (
          q.isError ? (
            <Empty>Could not load /api/discovery/summary: {String(q.error)}</Empty>
          ) : (
            <Empty>
              No discovery_log.json yet. Tournament appends a row after each
              name finishes. Empty means nothing has been recorded — not that
              discovery is running.
            </Empty>
          )
        ) : !viewed.length ? (
          <Empty>No already-tested rows match these column filters.</Empty>
        ) : (
          <>
            <PhoneCards>
              {visible.map((d, idx) => (
                <li key={`${d.strategy ?? "row"}-${d.tested_at ?? idx}`} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                  <MonoName className="block w-full text-xs font-medium text-white">{d.strategy ?? "—"}</MonoName>
                  <Badge tone={testedTone(d.qualified)}>{d.qualified ? "QUALIFIED" : "REJECTED"}</Badge>
                  <TestedRowFields d={d} />
                  {!d.qualified && d.fail_reasons?.length ? (
                    <p className="text-[11px] text-white/40 break-words">{d.fail_reasons.join("; ")}</p>
                  ) : null}
                  {windowDiagnostic(d) ? (
                    <p className="text-[11px] text-white/35 break-words">{windowDiagnostic(d)}</p>
                  ) : null}
                </li>
              ))}
            </PhoneCards>
            <DesktopTable>
              <table className="w-full text-xs">
                <thead className="text-white/40 uppercase">
                  <tr>
                    <SortTh label="Name" column="strategy" sort={sort} onCycle={cycleColumn} />
                    <SortTh label="When" column="testedAt" sort={sort} onCycle={cycleColumn} />
                    <SortTh label="Result" column="result" sort={sort} onCycle={cycleColumn} />
                    <SortTh label="Sharpe" column="sharpe" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="OOS trades" column="trades" sort={sort} onCycle={cycleColumn} align="right" />
                    <SortTh label="Test P&L" column="testPnl" sort={sort} onCycle={cycleColumn} align="right" />
                    <th className="text-left py-1 pl-3 whitespace-nowrap">Fail reasons</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((d, idx) => (
                    <tr key={`${d.strategy ?? "row"}-${d.tested_at ?? idx}`} className="border-t border-white/5">
                      <td className="py-1" title={d.strategy ?? undefined}>
                        <MonoName className="font-medium text-white">{d.strategy ?? "—"}</MonoName>
                      </td>
                      <td className="text-white/50">{fmtWhen(d.tested_at)}</td>
                      <td>
                        <Badge tone={testedTone(d.qualified)}>{d.qualified ? "QUALIFIED" : "REJECTED"}</Badge>
                      </td>
                      <td className="text-right font-mono">{d.sharpe != null ? d.sharpe.toFixed(2) : "—"}</td>
                      <td className="text-right font-mono">{d.trades ?? "—"}</td>
                      <td className={`text-right font-mono font-bold ${(d.test_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {fmt(d.test_pnl)}
                      </td>
                      <td className="text-white/40 break-words pl-3">
                        {[
                          !d.qualified && d.fail_reasons?.length ? d.fail_reasons.join("; ") : null,
                          windowDiagnostic(d),
                        ]
                          .filter(Boolean)
                          .join(" · ") || "—"}
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
      </section>

      <section className="min-w-0">
        <h3 className="text-xs uppercase tracking-wider text-white/40 mb-2">
          Not tested yet ({queued.length})
        </h3>
        {!queued.length ? (
          <Empty>
            No never-tested leftover names. Unique tested ({uniqueTested ?? tested.length})
            is latest-eval-per-name
            {logRows != null ? ` (${logRows} log rows)` : ""}
            {(counts?.rejected_parked ?? counts?.tested_fail ?? 0) > 0
              ? `. ${counts?.rejected_parked ?? counts?.tested_fail} already tested · rejected ${
                  (counts?.rejected_parked ?? counts?.tested_fail) === 1 ? "is" : "are"
                } parked forever — not coming back after a cooldown.`
              : ". Everything not already a champion or graduated has a row in already tested — or the universe is empty."}
          </Empty>
        ) : (
          <div className="flex flex-wrap gap-1.5 min-w-0">
            {queued.slice(0, shownQueued).map((name) => (
              <NameChip key={name} name={name} className="bg-white/10 text-white/80" />
            ))}
            {queued.length > shownQueued ? (
              <button
                type="button"
                onClick={() => setShownQueued((n) => n + PAGE)}
                className="min-h-11 px-3 py-2 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
              >
                Show more ({queued.length - shownQueued} left)
              </button>
            ) : null}
          </div>
        )}
      </section>
    </Card>
  );
}
