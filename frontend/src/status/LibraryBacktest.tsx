import type { LibraryBacktest } from "../api/types";
import { usd } from "../components/kit";

export function isLibraryName(name: string | null | undefined): boolean {
  return typeof name === "string" && name.startsWith("lib.");
}

function money(n: number | null | undefined): string {
  return usd(n, 2, true);
}

function tone(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n) || n === 0) return "text-white";
  return n > 0 ? "text-emerald-300" : "text-rose-300";
}

function Stat({ label, hint, value, valueTone }: { label: string; hint: string; value: string; valueTone?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wider text-amber-100/55">{label}</div>
      <div className={`mt-0.5 text-sm font-semibold tabular-nums ${valueTone ?? "text-white"}`}>{value}</div>
      <div className="mt-0.5 text-[11px] leading-snug text-white/40">{hint}</div>
    </div>
  );
}

/** Historical gate backtest for one lib.* champion. Live paper P&L stays in the block above. */
export function LibraryBacktestBox({ result }: { result: LibraryBacktest | null | undefined }) {
  const warnings = result?.warnings ?? [];
  return (
    <section
      className="rounded-xl border border-amber-400/30 bg-amber-500/[0.07] p-3"
      aria-label="Backtest (gate)"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-amber-200">Backtest (gate)</h3>
        <p className="text-[11px] text-white/45">Historical daily tape. Live paper P&amp;L is the block above.</p>
      </div>
      {result == null ? (
        <p className="mt-2 text-sm text-white/55">No discovery result is stored for this name yet.</p>
      ) : (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Stat
            label="Test-years net P&L"
            hint="2018-12-01 → 2025-09-30, compounded book (ending equity minus $10,000)"
            value={money(result.oos_net_pnl)}
            valueTone={tone(result.oos_net_pnl)}
          />
          <Stat
            label="Holdout P&L"
            hint={`2025-10-01 → 2026-10-01 · ${result.holdout_trades ?? "—"} trades`}
            value={money(result.holdout_pnl)}
            valueTone={tone(result.holdout_pnl)}
          />
          <Stat
            label="Stressed test P&L"
            hint="2× fees + 5 bps, same test years"
            value={money(result.cost_oos_pnl)}
            valueTone={tone(result.cost_oos_pnl)}
          />
          <Stat
            label="Stressed holdout P&L"
            hint="2× fees + 5 bps, holdout year"
            value={money(result.cost_holdout_pnl)}
            valueTone={tone(result.cost_holdout_pnl)}
          />
          <div className="min-w-0 sm:col-span-2">
            <div className="text-[11px] uppercase tracking-wider text-amber-100/55">Gate</div>
            <div className="mt-0.5 font-mono text-xs text-white/80 break-all">{result.gate_rules ?? "—"}</div>
          </div>
          <div className="min-w-0 sm:col-span-2">
            <div className="text-[11px] uppercase tracking-wider text-amber-100/55">Warnings</div>
            {warnings.length ? (
              <ul className="mt-1 space-y-1 text-xs text-amber-100/90">
                {warnings.map((warning) => (
                  <li key={warning} className="break-words">
                    {warning}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="mt-0.5 text-sm text-white/50">none</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
