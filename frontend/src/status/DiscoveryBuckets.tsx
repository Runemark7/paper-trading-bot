import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchDiscoverySummary } from "../api/client";
import type { DiscoveryEvaluation } from "../api/types";
import { Badge, Card, Empty, Field, FieldGrid, MonoName, PhoneCards, DesktopTable, fmt } from "../components/ui";
import { fmtWhen } from "./format";

const PAGE = 20;
type TestedFilter = "all" | "qualified" | "rejected";

function testedTone(ok: boolean): "pos" | "neg" {
  return ok ? "pos" : "neg";
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

export default function DiscoveryBuckets() {
  const q = useQuery({
    queryKey: ["discovery-summary"],
    queryFn: fetchDiscoverySummary,
    refetchInterval: 15_000,
  });
  const [filter, setFilter] = useState<TestedFilter>("all");
  const [shown, setShown] = useState(PAGE);

  const data = q.data;
  const tested = data?.tested ?? [];
  const filtered = useMemo(() => {
    if (filter === "qualified") return tested.filter((d) => d.qualified);
    if (filter === "rejected") return tested.filter((d) => !d.qualified);
    return tested;
  }, [tested, filter]);
  const visible = filtered.slice(0, shown);
  const flight = data?.in_flight;
  const queued = data?.queued ?? data?.untested ?? [];
  const counts = data?.counts;
  const uniqueTested = counts?.unique_tested ?? counts?.tested;
  const logRows = counts?.log_rows;
  const stuck = Boolean(data?.stuck || flight?.stale);

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
        job. Champions and graduated names stay in their sections above — they are
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
            {flight.names.length ? (
              <div className="flex flex-wrap gap-1.5 min-w-0">
                {flight.names.map((name) => (
                  <span
                    key={name}
                    className="inline-flex max-w-full items-center rounded-md bg-amber-500/15 px-1.5 py-0.5 font-mono text-[11px] text-amber-100 break-all"
                  >
                    {name}
                  </span>
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
          <div className="flex flex-wrap gap-1">
            {(["all", "qualified", "rejected"] as TestedFilter[]).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setFilter(key);
                  setShown(PAGE);
                }}
                className={`min-h-11 px-3 py-1.5 rounded text-xs capitalize ${
                  filter === key ? "bg-white/15 text-white" : "bg-white/5 text-white/60 hover:bg-white/10"
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
        <p className="text-xs text-white/45 mb-2">
          Already tested · rejected means parked forever (fail once). Those names
          are not retested and do not return after a cooldown.
        </p>

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
        ) : (
          <>
            <PhoneCards>
              {visible.map((d) => (
                <li key={`${d.strategy}-${d.tested_at}`} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                  <div className="flex items-start justify-between gap-2 min-w-0">
                    <MonoName className="block text-xs font-medium text-white min-w-0">{d.strategy}</MonoName>
                    <span className="shrink-0">
                      <Badge tone={testedTone(d.qualified)}>{d.qualified ? "QUALIFIED" : "REJECTED"}</Badge>
                    </span>
                  </div>
                  <TestedRowFields d={d} />
                  {!d.qualified && d.fail_reasons?.length ? (
                    <p className="text-[11px] text-white/40 break-words">{d.fail_reasons.join("; ")}</p>
                  ) : null}
                </li>
              ))}
            </PhoneCards>
            <DesktopTable>
              <table className="w-full text-xs">
                <thead className="text-white/40 uppercase">
                  <tr>
                    <th className="text-left py-1">Name</th>
                    <th className="text-left">When</th>
                    <th className="text-left">Result</th>
                    <th className="text-right">Sharpe</th>
                    <th className="text-right">OOS trades</th>
                    <th className="text-right">Test P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((d) => (
                    <tr key={`${d.strategy}-${d.tested_at}`} className="border-t border-white/5">
                      <td className="py-1 font-mono font-medium text-white break-all">{d.strategy}</td>
                      <td className="text-white/50">{fmtWhen(d.tested_at)}</td>
                      <td>
                        <Badge tone={testedTone(d.qualified)}>{d.qualified ? "QUALIFIED" : "REJECTED"}</Badge>
                      </td>
                      <td className="text-right font-mono">{d.sharpe != null ? d.sharpe.toFixed(2) : "—"}</td>
                      <td className="text-right font-mono">{d.trades ?? "—"}</td>
                      <td className={`text-right font-mono font-bold ${(d.test_pnl ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {fmt(d.test_pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </DesktopTable>
            {filtered.length > shown && (
              <button
                type="button"
                onClick={() => setShown((n) => n + PAGE)}
                className="mt-3 min-h-11 px-3 py-2 text-xs bg-white/10 hover:bg-white/20 rounded text-white"
              >
                Show more ({filtered.length - shown} left)
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
            {queued.map((name) => (
              <span
                key={name}
                className="inline-flex max-w-full items-center rounded-md bg-white/10 px-1.5 py-0.5 font-mono text-[11px] text-white/80 break-all"
              >
                {name}
              </span>
            ))}
          </div>
        )}
      </section>
    </Card>
  );
}
