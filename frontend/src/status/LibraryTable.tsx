import { useQuery } from "@tanstack/react-query";
import { fetchDiscoveryLibrary } from "../api/client";
import { pollInterval } from "../api/poll";
import type { LibraryResult, LibraryStrategyRow } from "../api/types";
import { Badge } from "../components/ui";
import { ErrorNote, Panel, SectionTitle, SkeletonRows, num } from "../components/kit";

const CHECK_LABEL: Record<string, string> = {
  windows: "windows",
  oos_trades: "OOS trades",
  oos_pnl: "OOS P&L",
  holdout_pnl: "holdout P&L",
  holdout_trades: "holdout trades",
  cost_stress: "cost stress",
};

const WARNING_LABEL: Record<string, string> = {
  oos_sharpe: "OOS Sharpe",
  beat_sma_stack: "vs sma_stack",
  holdout_sharpe: "holdout Sharpe",
  holdout_beat_sma_stack: "holdout vs sma_stack",
  dsr: "DSR",
  pbo: "PBO",
  neighbours: "neighbours",
  buy_and_hold: "vs buy-and-hold",
};

function paramsOf(row: LibraryStrategyRow): string {
  return row.params.map((p) => `${p.name}=${p.value}`).join(", ");
}

function citationTip(row: LibraryStrategyRow): string {
  const c = row.citation;
  return [
    c.title,
    c.venue,
    c.tested_rule && `Tested: ${c.tested_rule}`,
    c.implemented_rule && `Here: ${c.implemented_rule}`,
    c.differences && `Differences: ${c.differences}`,
  ]
    .filter(Boolean)
    .join("\n");
}

function verdict(result: LibraryResult): { text: string; tone: "pos" | "neg" | "warn" } {
  if (result.qualified) return { text: "Pass", tone: "pos" };
  const fails = (result.failing_checks ?? []).map((key) => CHECK_LABEL[key] ?? key);
  if (result.count_only) {
    return { text: `Fail, trade-count only${fails.length ? `: ${fails.join(", ")}` : ""}`, tone: "warn" };
  }
  return { text: fails.length ? `Fail: ${fails.join(", ")}` : "Fail", tone: "neg" };
}

function Cell({ children, title }: { children: string; title?: string }) {
  return (
    <td className="px-2 py-2 text-right tabular-nums text-white/80 whitespace-nowrap" title={title}>
      {children}
    </td>
  );
}

export default function LibraryTable() {
  const query = useQuery({
    queryKey: ["discovery-library"],
    queryFn: fetchDiscoveryLibrary,
    refetchInterval: pollInterval(60_000),
    refetchIntervalInBackground: false,
  });
  const data = query.data;
  const progress = data?.progress;

  return (
    <Panel>
      <SectionTitle
        title="Strategy library"
        aside={data?.gate_rules}
      >
        Cited daily BTC rules. A pass is profitable after fees on the test years and the holdout year. Neighbours are a warning. It is proposed, not seated.
      </SectionTitle>
      {query.isError ? <ErrorNote what="the strategy library" error={query.error} stale={Boolean(data)} /> : null}
      {query.isPending && !data ? <SkeletonRows rows={6} /> : null}
      {data ? (
        <>
          <p className="mb-3 text-xs text-white/50">
            {progress?.tested ?? 0} of {progress?.strategies ?? 0} tested
            {progress?.leased ? ` · ${progress.leased} testing` : ""}
            {" · "}
            {progress?.passed ?? 0} passed
            {" · "}
            DSR trial count {progress?.trial_count ?? "—"} (every grid point, not a search)
            {data.source ? ` · queue: ${data.source}` : ""}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-white/10 text-[10px] uppercase tracking-wider text-white/40">
                  <th className="px-2 py-2 font-medium">Strategy</th>
                  <th className="px-2 py-2 font-medium">Citation</th>
                  <th className="px-2 py-2 font-medium">Params</th>
                  <th className="px-2 py-2 text-right font-medium">OOS P&L</th>
                  <th className="px-2 py-2 text-right font-medium">Trades</th>
                  <th className="px-2 py-2 text-right font-medium">Holdout P&L</th>
                  <th className="px-2 py-2 text-right font-medium">Stressed P&L</th>
                  <th className="px-2 py-2 text-right font-medium">DSR</th>
                  <th className="px-2 py-2 text-right font-medium">PBO</th>
                  <th className="px-2 py-2 text-right font-medium">Buy & hold</th>
                  <th className="px-2 py-2 font-medium">Result</th>
                  <th className="px-2 py-2 font-medium">Warnings</th>
                </tr>
              </thead>
              <tbody>
                {data.strategies.map((row) => {
                  const result = row.result;
                  const outcome = result ? verdict(result) : null;
                  const neighbours =
                    result?.neighbours_pass != null && result.neighbours != null
                      ? `${result.neighbours_pass}/${result.neighbours} neighbours`
                      : undefined;
                  return (
                    <tr key={row.name} className="border-b border-white/[0.06] align-top">
                      <td className="px-2 py-2 min-w-[10rem]">
                        <div className="font-medium text-white">{row.title}</div>
                        <div className="font-mono text-[10px] text-white/40">{row.name}</div>
                      </td>
                      <td className="px-2 py-2 max-w-[14rem] text-white/70" title={citationTip(row)}>
                        {row.citation.link ? (
                          <a href={row.citation.link} className="underline decoration-white/20 underline-offset-2 hover:text-white" target="_blank" rel="noreferrer">
                            {row.citation.short}
                          </a>
                        ) : (
                          row.citation.short
                        )}
                      </td>
                      <td className="px-2 py-2 font-mono text-[10px] text-white/60 whitespace-nowrap">{paramsOf(row)}</td>
                      <Cell title={result?.daily_sharpe != null ? `OOS Sharpe ${num(result.daily_sharpe, 2)} (warning)` : undefined}>
                        {result ? num(result.oos_net_pnl, 0) : "—"}
                      </Cell>
                      <Cell>{result?.trades != null ? String(result.trades) : "—"}</Cell>
                      <Cell title={result?.holdout_sharpe != null ? `holdout Sharpe ${num(result.holdout_sharpe, 2)} (warning) · ${result.holdout_trades ?? "—"} trades` : undefined}>
                        {result ? `${num(result.holdout_pnl, 0)} / ${result.holdout_trades ?? "—"}` : "—"}
                      </Cell>
                      <Cell title={result?.cost_sharpe != null ? `stressed OOS Sharpe ${num(result.cost_sharpe, 2)} (warning)` : undefined}>
                        {result
                          ? `${num(result.cost_oos_pnl, 0)} / ${num(result.cost_holdout_pnl, 0)}`
                          : "—"}
                      </Cell>
                      <Cell title={result?.n_trials != null ? `N = ${result.n_trials}. Warning, not a veto.` : undefined}>
                        {result ? num(result.dsr, 3) : "—"}
                      </Cell>
                      <Cell title={neighbours ? `${neighbours}. Warning, not a veto.` : "Warning, not a veto."}>
                        {result ? num(result.pbo, 2) : "—"}
                      </Cell>
                      <Cell title="Buy-and-hold Sharpe. A loss to buy-and-hold is a warning, not a veto.">
                        {result ? num(result.bh_daily_sharpe, 2) : "—"}
                      </Cell>
                      <td className="px-2 py-2 min-w-[9rem]">
                        {outcome ? (
                          <Badge tone={outcome.tone}>{outcome.text}</Badge>
                        ) : (
                          <span className="text-white/40">{row.status === "testing" ? "testing" : "queued"}</span>
                        )}
                      </td>
                      <td className="px-2 py-2 min-w-[8rem] text-amber-100/80" title={result?.warnings?.join("\n")}>
                        {result
                          ? (result.warning_checks?.length
                            ? result.warning_checks.map((key) => WARNING_LABEL[key] ?? key).join(", ")
                            : "none")
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs leading-relaxed text-white/45">{data.note} Pass/fail uses the vetoes only. The warnings column is DSR, PBO, neighbours, Sharpe, sma_stack, and buy-and-hold.</p>
        </>
      ) : null}
    </Panel>
  );
}
