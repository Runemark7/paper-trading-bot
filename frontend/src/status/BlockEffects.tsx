import { useQuery } from "@tanstack/react-query";
import { fetchBlockReport } from "../api/client";
import type { BlockEffect, PairedAxisEffect } from "../api/types";
import { Badge, Card, Empty } from "../components/ui";

function num(value: number | null | undefined, digits = 3): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

function FamilyRow({ row }: { row: BlockEffect }) {
  const effect = row.depth_controlled_daily_sharpe;
  const tone = effect == null ? "neutral" : effect > 0 ? "pos" : effect < 0 ? "neg" : "neutral";
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
      <span className="font-mono text-white/80 break-all">{row.block}</span>
      <span className="text-white/50">
        n {row.n_with}/{row.n_without}
        {" · "}
        <span className={tone === "pos" ? "text-emerald-300" : tone === "neg" ? "text-rose-300" : ""}>
          {num(effect)}
        </span>
        {" daily Sharpe"}
        {" · "}
        pass {num(row.pass_rate_with, 2)} vs {num(row.pass_rate_without, 2)}
      </span>
    </div>
  );
}

function AxisRow({ row }: { row: PairedAxisEffect }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
      <span className="font-mono text-white/80">{row.axis}</span>
      <span className="text-white/50">
        n {row.n}
        {" · "}
        Δ {num(row.mean_daily_sharpe_delta)}
        {" · "}
        CI {num(row.ci95_low)} … {num(row.ci95_high)}
        {" · "}
        sign p {num(row.sign_test_p, 3)}
        {" · "}
        {row.n_positive}+ / {row.n_negative}− / {row.n_zero} ties
      </span>
    </div>
  );
}

/** Compact exploratory block effects. Not a gate and not the 15s summary poll. */
export default function BlockEffects() {
  const q = useQuery({
    queryKey: ["discovery-blocks"],
    queryFn: fetchBlockReport,
    refetchInterval: 60_000,
  });
  const families = (q.data?.families ?? []).slice(0, 8);
  const paired = q.data?.paired ?? [];

  return (
    <Card
      title="Block effects"
      aside={
        <Badge tone="warn">exploratory</Badge>
      }
    >
      <p className="text-sm text-white/55 mb-3">
        {q.data?.label ||
          "Exploratory. Pre-holdout results only. This report does not change the gate."}
      </p>
      {q.isError && (
        <div className="text-rose-300 text-sm mb-3">
          Could not load /api/discovery/blocks: {String(q.error)}
        </div>
      )}
      {q.isSuccess && q.data.n_results === 0 && (
        <Empty>No current-gate pre-holdout results yet.</Empty>
      )}
      {families.length > 0 && (
        <div className="space-y-1.5 mb-3">
          <div className="text-xs uppercase tracking-wider text-white/40">
            Families, depth-matched daily Sharpe
          </div>
          {families.map((row) => (
            <FamilyRow key={row.block} row={row} />
          ))}
        </div>
      )}
      {paired.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-xs uppercase tracking-wider text-white/40">
            Paired swaps, child − parent
          </div>
          {paired.map((row) => (
            <AxisRow key={row.axis} row={row} />
          ))}
        </div>
      )}
    </Card>
  );
}
