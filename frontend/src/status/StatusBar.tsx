import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { pollInterval, STATUS_POLL_MS } from "../api/poll";
import { StatusDot, shortAgo } from "../components/kit";

/** One-line health strip: is the API up, is the live paper book cycling, is discovery producing results. */
export default function StatusBar() {
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: pollInterval(STATUS_POLL_MS),
  });
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 60_000 });

  if (status.isError) {
    return (
      <div className="border-b border-white/[0.07] bg-amber-500/10 px-4 py-2 md:px-6 text-xs text-amber-100 break-words">
        <div className="max-w-7xl mx-auto">
          Status API unavailable — live book and discovery state unknown right now. {String(status.error)}
        </div>
      </div>
    );
  }

  const run = status.data?.running_now;
  const pipe = status.data?.in_progress;
  const cycle = run?.cycle;
  const lots = run ? (run.open_lots ?? run.positions_open) : null;
  const cycleTone = !run ? "idle" : cycle?.next.overdue ? "warn" : "pos";
  const disc = pipe?.discovery;
  const discTone = !pipe ? "idle" : disc?.stuck || pipe.pipeline.stale ? "warn" : disc?.last_tested_at ? "pos" : "idle";

  return (
    <div className="border-b border-white/[0.07] bg-[#0d1430]/60 px-4 md:px-6">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center gap-x-5 gap-y-1 py-2 text-xs text-white/60 min-w-0">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot tone={health.data?.ok ? "pos" : health.isError ? "neg" : "idle"} />
          {health.data?.ok ? "API up" : health.isError ? "API down" : "API …"}
        </span>
        <Link to="/champions" title="Running now: the live paper book" className="inline-flex items-center gap-1.5 min-w-0 hover:text-white">
          <StatusDot tone={cycleTone} />
          <span className="min-w-0">
            <span className="text-white/80">Live book</span>
            {run ? (
              <>
                {" "}
                · {run.strategy.mode === "sma_stack_fallback" ? "sma_stack fallback" : `${run.strategy.active.length} champion${run.strategy.active.length === 1 ? "" : "s"}`}
                {" "}· {lots} open lots · last cycle {shortAgo(cycle?.last_cycle_at)}
                {cycle?.next.overdue ? " (overdue)" : ""}
              </>
            ) : (
              " · loading…"
            )}
          </span>
        </Link>
        <Link to="/discovery" title="In progress: the discovery pipeline" className="inline-flex items-center gap-1.5 min-w-0 hover:text-white">
          <StatusDot tone={discTone} />
          <span className="min-w-0">
            <span className="text-white/80">Discovery</span>
            {pipe ? (
              <>
                {" "}· last test {shortAgo(disc?.last_tested_at)}
                {disc?.stuck || pipe.pipeline.stale ? " · stuck / overdue" : ""}
              </>
            ) : (
              " · loading…"
            )}
          </span>
        </Link>
      </div>
    </div>
  );
}
