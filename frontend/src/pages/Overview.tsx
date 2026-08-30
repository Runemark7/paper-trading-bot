import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Card, Stat, fmt, fmtPct, Badge, Section, Empty, MonoName, Field, PhoneCards, DesktopTable } from "../components/ui";
import {
  accountLabel,
  cadenceLabel,
  certaintyLabel,
  fmtWhen,
  positionEntry,
  positionPnlPct,
} from "../status/format";

export default function Overview() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  const live = useQuery({ queryKey: ["live"], queryFn: api.live });
  const regime = useQuery({ queryKey: ["regime"], queryFn: api.regime });
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  const s = summary.data;
  const l = live.data;
  const r = regime.data;
  const run = status.data?.running_now;
  const prog = status.data?.in_progress;
  const cycle = run?.cycle;
  const hb = run?.heartbeat;

  return (
    <div className="space-y-6 min-w-0">
      <p className="text-sm text-white/55">
        Paper only. Glance here for what is on the live paper book versus last-known
        pipeline work. Spinners appear only from a sidecar stamp — never invented.
      </p>

      <Section
        tone="running"
        kicker="Running now"
        title="Live paper book"
        aside={status.data ? `as of ${fmtWhen(status.data.as_of)}` : "loading…"}
      >
        {status.isError && (
          <div className="text-rose-300 text-sm mb-3">Could not load /api/status: {String(status.error)}</div>
        )}
        <div className="flex flex-wrap gap-2 mb-4">
          <Badge tone="run">on the book</Badge>
          <Badge tone="neutral">{run?.strategy.mode === "sma_stack_fallback" ? "strategy: sma_stack fallback" : "strategy: champion accounts"}</Badge>
          <Badge tone={run?.positions_open ? "pos" : "neutral"}>
            {run ? `${run.positions_open} open` : "open …"}
          </Badge>
          <Badge tone={hb?.last_pass_at ? (hb.recent ? "pos" : "neutral") : "warn"}>
            heartbeat: {hb?.last_pass_at ? (hb.recent ? "recent stamp" : "last-known stamp") : "no stamp"}
          </Badge>
          <Badge tone="neutral">
            cycle {cycle ? cadenceLabel(cycle.interval_seconds) : "…"} · {cycle?.bar_timeframe ?? "4h"} bars
          </Badge>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm mb-4">
          <div>
            <div className="text-white/40 text-xs uppercase tracking-wider mb-1">Active on the book</div>
            <div className="font-mono text-white break-words [overflow-wrap:anywhere]">
              {(run?.strategy.active ?? []).join(", ") || "—"}
            </div>
            <div className="text-white/40 text-xs mt-1">
              {run?.accounts.count ?? "…"} {run?.accounts.kind ?? "accounts"} · {run?.strategy.source}
            </div>
          </div>
          <div>
            <div className="text-white/40 text-xs uppercase tracking-wider mb-1">Cycle</div>
            <div>Last cycle: {fmtWhen(cycle?.last_cycle_at)}</div>
            <div>
              Next (inferred): {cycle?.next.at ? fmtWhen(cycle.next.at) : "unknown"}
              {cycle?.next.overdue ? " — interval elapsed" : ""}
            </div>
            <div className="text-white/40 text-xs mt-1">
              Sidecar window {cycle?.window ?? "07–21 Stockholm"}. {cycle?.next.note}
            </div>
          </div>
          <div>
            <div className="text-white/40 text-xs uppercase tracking-wider mb-1">Heartbeat / stops</div>
            <div>
              {hb?.role}. Last pass: {fmtWhen(hb?.last_pass_at)}. Certainty: {certaintyLabel(hb?.certainty)}.
            </div>
            <div className="text-white/40 text-xs mt-1">{hb?.note}</div>
          </div>
          <div>
            <div className="text-white/40 text-xs uppercase tracking-wider mb-1">Regime (display only)</div>
            <div>
              Zone {r?.zone ?? "…"}. Does not gate the live book (<code>regime=None</code>).
            </div>
            <div className="text-white/40 text-xs mt-1">{run?.regime.note}</div>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mb-4">
          <Stat
            label="Paper equity"
            value={l ? fmt(l.live_equity, 0) : "…"}
            hint="marked from /api/live"
          />
          <Stat
            label="Closed trades"
            value={s ? String(s.closed_trades) : "…"}
            hint={s?.source ?? "same paper accounts"}
          />
          <Stat label="Win rate" value={s ? fmtPct(s.win_rate) : "…"} hint={s?.updated ? `last snapshot ${fmtWhen(s.updated)}` : undefined} />
          <Stat
            label="Closed P&L"
            value={s?.total_pnl != null ? fmt(s.total_pnl) : "—"}
            tone={s?.total_pnl ? (s.total_pnl >= 0 ? "pos" : "neg") : "neutral"}
            hint="closed paper trades only"
          />
        </div>

        <Card title="Open positions on the paper book" aside={l?.as_of ? `prices ${l.as_of}` : undefined}>
          {!l?.positions?.length ? (
            <Empty>
              No open lots. The book is still the live experiment — last cycle {fmtWhen(cycle?.last_cycle_at)}.
              Empty is a state, not a missing feed.
            </Empty>
          ) : (
            <>
              <PhoneCards>
                {l.positions.map((p, i) => {
                  const pnlPct = positionPnlPct(p);
                  return (
                    <li key={i} className="rounded-lg border border-white/10 p-3 space-y-2">
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="font-medium">{p.symbol}</span>
                        <span className={`shrink-0 ${(pnlPct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {fmtPct(pnlPct)}
                        </span>
                      </div>
                      <MonoName className="text-xs text-white/50">{accountLabel(p.account)}</MonoName>
                      <div className="grid grid-cols-2 gap-2">
                        <Field label="Condition">{p.condition ?? "—"}</Field>
                        <Field label="Entry">{fmt(positionEntry(p))}</Field>
                        <Field label="Now">{p.current != null ? fmt(p.current) : "—"}</Field>
                      </div>
                    </li>
                  );
                })}
              </PhoneCards>
              <DesktopTable>
                <table className="w-full text-sm">
                  <thead className="text-white/40 text-xs uppercase">
                    <tr>
                      <th className="text-left py-2">Account</th>
                      <th className="text-left">Symbol</th>
                      <th className="text-left">Condition</th>
                      <th className="text-right">Entry</th>
                      <th className="text-right">Now</th>
                      <th className="text-right">P&L %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {l.positions.map((p, i) => {
                      const pnlPct = positionPnlPct(p);
                      return (
                        <tr key={i} className="border-t border-white/5">
                          <td className="py-2 font-mono text-xs">{accountLabel(p.account)}</td>
                          <td className="font-medium">{p.symbol}</td>
                          <td className="text-white/60">{p.condition ?? "—"}</td>
                          <td className="text-right">{fmt(positionEntry(p))}</td>
                          <td className="text-right">{p.current != null ? fmt(p.current) : "—"}</td>
                          <td className={`text-right ${(pnlPct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                            {fmtPct(pnlPct)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </DesktopTable>
            </>
          )}
        </Card>
      </Section>

      <Section
        tone="progress"
        kicker="In progress"
        title="Pipeline (not the live book)"
        aside="last-known · no job runner"
      >
        {!prog ? (
          <Empty>Loading last-known pipeline state…</Empty>
        ) : (
          <div className="space-y-4 text-sm">
            <p className="text-white/50">{prog.note}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <PipelineRow
                title="Sidecar cycle stamp"
                chip={
                  prog.pipeline.stamp_says_in_progress
                    ? `stamp: ${prog.pipeline.phase}`
                    : prog.pipeline.phase
                      ? `last phase ${prog.pipeline.phase}`
                      : "no stamp"
                }
                tone={prog.pipeline.stale ? "warn" : prog.pipeline.stamp_says_in_progress ? "wait" : "neutral"}
                body={
                  prog.pipeline.certainty === "no_signal"
                    ? prog.pipeline.note
                    : `Started ${fmtWhen(prog.pipeline.started_at)}. Finished ${fmtWhen(prog.pipeline.finished_at)}. ${prog.pipeline.note}`
                }
              />
              <PipelineRow
                title="Discovery / tournament qualification"
                chip={prog.discovery.last_tested_at ? `last ${fmtWhen(prog.discovery.last_tested_at)}` : "no log"}
                tone="neutral"
                body={
                  prog.discovery.last_strategy
                    ? `${prog.discovery.last_strategy} · ${prog.discovery.last_qualified ? "qualified" : "rejected"} · ${prog.discovery.log_count} logged. ${prog.discovery.note ?? ""}`
                    : (prog.discovery.note ?? "No discovery log yet.")
                }
              />
              <PipelineRow
                title="Replenish"
                chip={
                  prog.replenish.stamp_says_this_phase
                    ? "stamp says tournament/replenish"
                    : prog.replenish.needed
                      ? `${prog.replenish.slots_open} slots open`
                      : "pool at capacity"
                }
                tone={prog.replenish.stamp_says_this_phase ? "wait" : "neutral"}
                body={prog.replenish.note ?? "Slots open does not mean replenish is running."}
              />
              <PipelineRow
                title="Graduation → GRADUATED_PAPER"
                chip={
                  prog.graduation.last_graduated_at
                    ? `${prog.graduation.last_status ?? "done"} ${fmtWhen(prog.graduation.last_graduated_at)}`
                    : "none yet"
                }
                tone="neutral"
                body={
                  prog.graduation.last_name
                    ? `${prog.graduation.last_name} · ${prog.graduation.count} total. ${prog.graduation.note ?? ""}`
                    : (prog.graduation.note ?? "No graduations recorded.")
                }
              />
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}

function PipelineRow({
  title,
  chip,
  tone,
  body,
}: {
  title: string;
  chip: string;
  tone: "wait" | "warn" | "neutral";
  body: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-white/10 p-3">
      <div className="flex flex-col gap-1.5 mb-1 sm:flex-row sm:items-start sm:justify-between sm:gap-2">
        <div className="font-medium text-white">{title}</div>
        <Badge tone={tone}>{chip}</Badge>
      </div>
      <div className="text-white/50 text-xs leading-relaxed break-words [overflow-wrap:anywhere]">{body}</div>
    </div>
  );
}
