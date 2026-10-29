import type { DiscoveryInsights, InsightRow } from "../api/types";
import { BlockChips, EmptyState, GatePill, InfoTip, Panel, RuleList, SectionTitle, SkeletonRows, num, shortAgo, utcStamp } from "../components/kit";
import { MonoName } from "../components/ui";
import { GATES, GATE_THRESHOLDS, HOLDOUT_SPAN, WARNING_GATES, gateInfo } from "../lib/gates";

/** Gate v7b vetoes, plus the warnings that are stored and never reject a library name. */
export function GateLegend({ insights }: { insights?: DiscoveryInsights }) {
  const counts = new Map((insights?.gate_failures ?? []).map((g) => [g.gate, g.count]));
  const max = Math.max(1, ...GATES.map((g) => counts.get(g.key) ?? 0));
  const tested = insights?.current_gate.tested;
  const groups: { title: string; note: string; stage: "floor" | "robustness" }[] = [
    { title: "Vetoes · profit and sample size", note: "A miss here rejects the library name.", stage: "floor" },
    { title: "Vetoes · holdout and costs", note: "Still required. Sharpe and neighbours are warnings.", stage: "robustness" },
  ];
  return (
    <Panel>
      <SectionTitle
        title="Gate v7b: profit after fees. Neighbours warn."
        aside={insights ? `rejections among ${tested?.toLocaleString("en-US")} names under the current gate` : undefined}
        info={
          <InfoTip label="Gate v7" align="left">
            The daily library passes when it is profitable after fees on the test years and the holdout year, including at higher costs, with the trade floors. Neighbours, DSR, PBO, Sharpe, sma_stack, and buy-and-hold are warnings. A pass is proposed, not seated. The 5m path is unchanged.
          </InfoTip>
        }
      >
        A library name is proposed only when every veto passes. Warnings are shown on the row and do not reject it.
      </SectionTitle>
      <div className="grid gap-4 lg:grid-cols-2">
        {groups.map((group) => (
          <div key={group.stage} className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wider text-white/50">{group.title}</div>
            <div className="text-xs text-white/40 mb-2">{group.note}</div>
            <ul className="space-y-2">
              {GATES.filter((g) => g.stage === group.stage).map((g) => {
                const n = counts.get(g.key) ?? 0;
                return (
                  <li key={g.key} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                      <span className="text-sm font-semibold text-white">{g.label}</span>
                      <span className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-white/70">{g.rule}</span>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-white/55">{g.plain}</p>
                    {insights ? (
                      <div className="mt-2 flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-white/5">
                          <div className="h-full rounded-full bg-rose-400/70" style={{ width: `${Math.round((n / max) * 100)}%` }} />
                        </div>
                        <span className="w-28 shrink-0 text-right text-[11px] tabular-nums text-white/50">
                          rejected {n.toLocaleString("en-US")}
                        </span>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
      <div className="mt-4 grid gap-2 sm:grid-cols-2 text-xs text-white/55">
        <div className="rounded-lg bg-white/[0.03] px-3 py-2">
          <span className="font-semibold text-white/80">Warnings, not vetoes.</span>{" "}
          {WARNING_GATES.map((g) => g.label).join(", ")}. Each is computed and shown. None of them rejects a library name.
        </div>
        <div className="rounded-lg bg-white/[0.03] px-3 py-2">
          <span className="font-semibold text-white/80">Sealed holdout {HOLDOUT_SPAN}.</span> That year never feeds the search, so
          it is a fair final exam.
        </div>
      </div>
    </Panel>
  );
}

function Metric({ label, value, ok, hint }: { label: string; value: string; ok: boolean | null; hint: string }) {
  const color = ok == null ? "text-white/70" : ok ? "text-emerald-300" : "text-rose-300";
  return (
    <div className="min-w-0 rounded-lg bg-white/[0.03] px-2 py-1.5" title={hint}>
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${color}`}>{value}</div>
    </div>
  );
}

function NearMissCard({ row, rank }: { row: InsightRow; rank: number }) {
  const g = row.gate;
  const nbFrac = g?.neighbours ? (g.neighbours_pass ?? 0) / g.neighbours : null;
  return (
    <li className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 sm:p-4 min-w-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[11px] text-white/40">
            #{rank} · tested {shortAgo(row.tested_at)}
          </div>
          <MonoName className="mt-0.5 block text-xs text-white/80">{row.strategy ?? "—"}</MonoName>
        </div>
      </div>
      <div className="mt-2">
        <RuleList name={row.strategy ?? ""} dense />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] uppercase tracking-wider text-white/40 mr-1">Failed</span>
        {row.failed_gates.map((key) => (
          <GatePill key={key} gate={key} />
        ))}
      </div>
      <div className="mt-3 grid grid-cols-3 sm:grid-cols-6 gap-1.5">
        <Metric label="Sharpe" value={num(row.sharpe)} ok={row.sharpe != null ? row.sharpe >= GATE_THRESHOLDS.sharpe : null} hint="walk-forward Sharpe, needs ≥ 0.30" />
        <Metric label="Trades" value={row.trades != null ? String(row.trades) : "—"} ok={row.trades != null ? row.trades >= GATE_THRESHOLDS.trades : null} hint="out-of-sample round-trips, daily library needs ≥ 8" />
        <Metric label="Holdout" value={num(g?.holdout_sharpe)} ok={g?.holdout_sharpe != null ? !row.failed_gates.includes("holdout") : null} hint="Sharpe on the sealed holdout year" />
        <Metric label="DSR" value={num(g?.dsr, 3)} ok={g?.dsr != null ? g.dsr >= GATE_THRESHOLDS.dsr : null} hint="deflated Sharpe, needs ≥ 0.95" />
        <Metric label="PBO" value={num(g?.pbo, 2)} ok={g?.pbo != null ? g.pbo <= GATE_THRESHOLDS.pbo : null} hint="overfitting probability, needs ≤ 0.20" />
        <Metric
          label="Neighbours"
          value={nbFrac != null ? `${Math.round(nbFrac * 100)}%` : "—"}
          ok={nbFrac != null ? nbFrac >= GATE_THRESHOLDS.neighbours : null}
          hint="share of nudged variants that still pass, needs ≥ 70%"
        />
      </div>
      {row.fail_reasons.length ? (
        <p className="mt-2 font-mono text-[11px] text-white/35 break-words">{row.fail_reasons.join(" · ")}</p>
      ) : null}
    </li>
  );
}

export function NearMisses({ insights, loading }: { insights?: DiscoveryInsights; loading: boolean }) {
  const rows = insights?.near_misses ?? [];
  return (
    <Panel>
      <SectionTitle
        title="Closest near-misses"
        aside={insights ? `${insights.current_gate.cleared_base_floors.toLocaleString("en-US")} names reached step B` : undefined}
      >
        Strategies that cleared every basic floor, then failed the fewest robustness checks. Red marks the check that stopped them.
      </SectionTitle>
      {loading ? (
        <SkeletonRows rows={3} />
      ) : !rows.length ? (
        <EmptyState title="No near-misses yet">
          No name under the current gate has cleared the basic floors without passing. Near-misses appear here once one gets to step B.
        </EmptyState>
      ) : (
        <ol className="grid gap-3 lg:grid-cols-2">
          {rows.map((row, i) => (
            <NearMissCard key={row.strategy ?? i} row={row} rank={i + 1} />
          ))}
        </ol>
      )}
    </Panel>
  );
}

export function RecentTests({ insights, loading }: { insights?: DiscoveryInsights; loading: boolean }) {
  const rows = insights?.recent ?? [];
  return (
    <Panel>
      <SectionTitle title="Latest tests" aside={insights ? `as of ${shortAgo(insights.as_of)}` : undefined}>
        The newest finished backtests and where each one stopped.
      </SectionTitle>
      {loading ? (
        <SkeletonRows rows={4} />
      ) : !rows.length ? (
        <EmptyState title="Nothing tested yet">Results appear here as workers post them.</EmptyState>
      ) : (
        <ul className="divide-y divide-white/[0.06]">
          {rows.map((row, i) => {
            const first = row.failed_gates[0];
            return (
              <li key={`${row.strategy}-${i}`} className="flex flex-col gap-1.5 py-2.5 sm:flex-row sm:items-center sm:gap-4 min-w-0">
                <div className="w-20 shrink-0 text-xs text-white/45" title={utcStamp(row.tested_at)}>
                  {shortAgo(row.tested_at)}
                </div>
                <div className="min-w-0 flex-1">
                  <BlockChips name={row.strategy ?? ""} />
                </div>
                <div className="flex shrink-0 items-center gap-3 text-xs tabular-nums text-white/55">
                  <span title="walk-forward Sharpe">S {num(row.sharpe)}</span>
                  <span title="out-of-sample trades">{row.trades ?? "—"} tr</span>
                  {row.qualified ? (
                    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-200">Passed</span>
                  ) : (
                    <span className="rounded-full bg-rose-500/10 px-2 py-0.5 text-[11px] font-medium text-rose-200" title={row.fail_reasons.join("; ")}>
                      {first ? gateInfo(first).label : "Rejected"}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
