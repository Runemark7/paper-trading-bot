import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge } from "../components/ui";
import { cadenceLabel, certaintyLabel, fmtWhen } from "./format";

export default function StatusBar() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 15_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 60_000 });

  if (status.isError) {
    return (
      <div className="border-b border-white/10 bg-[#0d1430] px-4 py-2 md:px-6 text-sm text-amber-200 break-words">
        Status API unavailable — cannot tell running vs in progress. {String(status.error)}
      </div>
    );
  }

  const d = status.data;
  const run = d?.running_now;
  const pipe = d?.in_progress;
  const cycle = run?.cycle;
  const hb = run?.heartbeat;

  return (
    <div className="border-b border-white/10 bg-[#0d1430] px-4 py-2 md:px-6">
      <div className="max-w-7xl mx-auto min-w-0 flex flex-col gap-2 text-xs md:flex-row md:flex-wrap md:items-start md:gap-x-3 md:gap-y-2">
        <div className="min-w-0 flex flex-wrap items-start gap-x-2 gap-y-1">
          <Badge tone={health.data?.ok ? "pos" : "neg"}>
            {health.data?.ok ? "API up" : "API down"}
          </Badge>
          <Badge tone="run">Running now</Badge>
          {run ? (
            <>
              <span className="text-white/80 min-w-0 break-words">
                {run.accounts.count} paper account{run.accounts.count === 1 ? "" : "s"}
                {run.strategy.mode === "sma_stack_fallback" ? " · sma_stack fallback" : ""}
                {run.strategy.active.length === 1 ? ` · ${run.strategy.active[0]}` : ` · ${run.strategy.active.length} champions`}
              </span>
              <span className="text-white/70 min-w-0 break-words">
                last cycle {fmtWhen(cycle?.last_cycle_at)} · next {cycle?.next.at ? fmtWhen(cycle.next.at) : "unknown"} ({certaintyLabel(cycle?.next.inferred ? "inferred" : "last_known")})
              </span>
              <span className="text-white/70 min-w-0 break-words">
                {run.positions_open} open · heartbeat {hb?.last_pass_at ? fmtWhen(hb.last_pass_at) : "no stamp"}
              </span>
            </>
          ) : (
            <span className="text-white/50">Loading paper-book status…</span>
          )}
        </div>
        <div
          className="min-w-0 flex flex-wrap items-start gap-x-2 gap-y-1 border-t border-white/10 pt-2 md:border-t-0 md:pt-0 md:border-l md:border-white/20 md:pl-3"
          aria-label="In progress"
        >
          <Badge tone="wait">In progress</Badge>
          {pipe ? (
            <span className="text-white/70 min-w-0 break-words">
              {pipe.pipeline.stamp_says_in_progress
                ? `stamp: ${pipe.pipeline.phase} since ${fmtWhen(pipe.pipeline.started_at)} — liveness not verified`
                : pipe.discovery.last_tested_at
                  ? `last discovery ${fmtWhen(pipe.discovery.last_tested_at)}`
                  : "no pipeline stamp · last-known files only"}
              {cycle ? ` · cadence ${cadenceLabel(cycle.interval_seconds)}` : ""}
            </span>
          ) : (
            <span className="text-white/50">…</span>
          )}
        </div>
      </div>
    </div>
  );
}
