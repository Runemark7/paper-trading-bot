import type { BacktestParam, Champion, LegacyBacktest, LibraryBacktest } from "../api/types";
import { num, usd } from "../components/kit";
import { strategyBlocks } from "../lib/rules";
import { fmtChampionSince } from "./format";

const OOS_SPAN = "2018-12-01 to 2025-09-30";
const HOLDOUT_SPAN = "2025-10-01 to 2026-10-01";

export function isLibraryName(name: string | null | undefined): boolean {
  return typeof name === "string" && name.startsWith("lib.");
}

function finite(n: number | null | undefined): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

function money(n: number): string {
  return usd(n, 2, true);
}

function tone(n: number): string {
  if (n === 0) return "text-white";
  return n > 0 ? "text-emerald-300" : "text-rose-300";
}

function Amount({ n }: { n: number }) {
  return <span className={`font-semibold tabular-nums ${tone(n)}`}>{money(n)}</span>;
}

function paramLine(params: BacktestParam[] | null | undefined): string | null {
  if (!params?.length) return null;
  return params
    .filter((p) => p && typeof p.name === "string" && finite(p.value))
    .map((p) => `${p.name}=${p.value}${p.unit ? ` ${p.unit}` : ""}`)
    .join(", ");
}

function safeLink(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:") return parsed.toString();
  } catch {
    return null;
  }
  return null;
}

function warningNote(result: LibraryBacktest): string {
  const bits: string[] = [];
  if (finite(result.dsr)) bits.push(`DSR ${num(result.dsr, 2)}`);
  if (finite(result.pbo)) bits.push(`PBO ${num(result.pbo, 2)}`);
  if (finite(result.daily_sharpe)) bits.push(`test-year Sharpe ${num(result.daily_sharpe, 2)}`);
  if (finite(result.holdout_sharpe)) bits.push(`holdout Sharpe ${num(result.holdout_sharpe, 2)}`);
  const checks = result.warning_checks ?? [];
  if (checks.includes("neighbours") && !bits.some((bit) => bit.toLowerCase().includes("neighbour"))) {
    bits.push("parameter neighbours");
  }
  if (bits.length) return bits.join(" · ");
  const lines = (result.warnings ?? []).filter((line) => !/buy-and-hold/i.test(line));
  return lines.join(" · ");
}

function LibraryBacktestBox({ champ }: { champ: Champion }) {
  const result = champ.library_result;
  const params = paramLine(result?.params);
  const link = safeLink(result?.citation_link);
  const warnings = result ? warningNote(result) : "";
  const seated = fmtChampionSince(champ.champion_since);
  return (
    <section className="rounded-xl border border-amber-400/30 bg-amber-500/[0.07] p-3" aria-label="Backtest">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-amber-200">Backtest</h3>
      <p className="mt-1 text-[13px] leading-relaxed text-white/70">
        Daily Bitcoin, all-in or flat. A fill happens at the next open after a 0.1% fee and 2 bps slippage.
        The position closes when the signal turns off, or on a{" "}
        {finite(result?.safety_stop_pct) ? `${result.safety_stop_pct}%` : "20%"} stop.
        This is the historical test, not the live paper account above.
      </p>
      {result?.rule ? (
        <p className="mt-2 text-sm leading-relaxed text-white">
          {result.title ? <span className="font-semibold">{result.title}. </span> : null}
          {result.rule}
        </p>
      ) : (
        <p className="mt-2 text-sm text-white/55">The strategy sentence was not stored for this name.</p>
      )}
      <p className="mt-1 text-[12px] leading-relaxed text-white/55">
        {params ? <span>Params {params}. </span> : null}
        {result?.citation ? (
          <>
            Cited{" "}
            {link ? (
              <a href={link} className="underline decoration-white/20 underline-offset-2 hover:text-white" target="_blank" rel="noreferrer">
                {result.citation}
              </a>
            ) : (
              result.citation
            )}
            .{" "}
          </>
        ) : null}
        Seated {seated}.
      </p>
      {result == null ? (
        <p className="mt-2 text-sm text-white/55">No discovery result is stored for this name yet.</p>
      ) : (
        <div className="mt-3 space-y-1.5 text-sm leading-relaxed text-white/80">
          <p>
            Out of sample, {OOS_SPAN}, after fees:{" "}
            {finite(result.oos_net_pnl) ? <Amount n={result.oos_net_pnl} /> : "not stored"}.
            {" "}
            Buy-and-hold over that same span:{" "}
            {finite(result.bh_book_pnl) ? <Amount n={result.bh_book_pnl} /> : "not stored"}.
          </p>
          <p>
            Holdout year, {HOLDOUT_SPAN}:{" "}
            {finite(result.holdout_pnl) ? <Amount n={result.holdout_pnl} /> : "not stored"}
            {finite(result.holdout_trades) ? ` in ${result.holdout_trades} trade${result.holdout_trades === 1 ? "" : "s"}` : ""}.
            {finite(result.bh_holdout_pnl) ? (
              <>
                {" "}Buy-and-hold that year: <Amount n={result.bh_holdout_pnl} />.
              </>
            ) : null}
          </p>
          <p>
            Higher costs, 2× fee + 5 bps:{" "}
            {finite(result.cost_oos_pnl) ? <Amount n={result.cost_oos_pnl} /> : "not stored"} out of sample
            {finite(result.cost_holdout_pnl) ? (
              <>
                {" "}and <Amount n={result.cost_holdout_pnl} /> in the holdout
              </>
            ) : null}
            .
          </p>
          <p>
            Total trades on the test years: {finite(result.trades) ? result.trades : "not stored"}.
          </p>
        </div>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-white/45">
        <span className="font-semibold uppercase tracking-wider text-amber-100/70">Warnings. </span>
        {warnings ? `${warnings}. ` : "No DSR, PBO, or Sharpe warnings were stored. "}
        These do not decide whether the strategy passed.
      </p>
    </section>
  );
}

function hasLegacyNumber(row: LegacyBacktest): boolean {
  return (
    finite(row.test_pnl) ||
    finite(row.trades) ||
    finite(row.sharpe) ||
    finite(row.win_rate_pct) ||
    finite(row.daily_sharpe) ||
    finite(row.bh_daily_sharpe) ||
    finite(row.bh_oos_pnl)
  );
}

function LegacyBacktestBox({ champ }: { champ: Champion }) {
  const row = champ.legacy_backtest;
  const rule = strategyBlocks(champ.name).map((block) => block.text).filter(Boolean).join("; ");
  const seated = fmtChampionSince(champ.champion_since);
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-3" aria-label="Legacy 5-minute backtest">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-white/70">Legacy 5-minute backtest</h3>
      <p className="mt-1 text-[13px] leading-relaxed text-white/65">
        This champion was admitted from the older 5-minute search, before the daily library.
        That test used walk-forward windows, an ATR stop, and a 2:1 target. It is not the
        daily gate, and figures below are only what was stored with the name.
      </p>
      {rule ? <p className="mt-2 text-sm leading-relaxed text-white">{rule}.</p> : null}
      <p className="mt-1 text-[12px] text-white/55">
        {row?.timeframe ? `${row.timeframe} bars. ` : null}
        Seated {seated}.
      </p>
      {row && hasLegacyNumber(row) ? (
        <div className="mt-3 space-y-1.5 text-sm leading-relaxed text-white/80">
          {finite(row.test_pnl) ? (
            <p>
              Walk-forward test P&amp;L: <Amount n={row.test_pnl} />.
              {finite(row.bh_oos_pnl) ? (
                <>
                  {" "}Buy-and-hold over those windows: <Amount n={row.bh_oos_pnl} />.
                </>
              ) : null}
            </p>
          ) : null}
          {finite(row.trades) ? <p>Trades in that test: {row.trades}.</p> : null}
          {finite(row.sharpe) ? <p>Qualification Sharpe: {num(row.sharpe, 2)}.</p> : null}
          {finite(row.win_rate_pct) ? <p>Win rate in the qualification: {num(row.win_rate_pct, 1)}%.</p> : null}
          {finite(row.daily_sharpe) ? (
            <p>
              Daily Sharpe {num(row.daily_sharpe, 2)}
              {finite(row.bh_daily_sharpe) ? `, buy-and-hold ${num(row.bh_daily_sharpe, 2)}` : ""}.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-2 text-sm text-white/55">No P&amp;L, trade count, or Sharpe was stored for this champion.</p>
      )}
    </section>
  );
}

/** Historical test for one champion. Live paper P&L stays in the block above. */
export function ChampionBacktest({ champ }: { champ: Champion }) {
  if (isLibraryName(champ.name)) return <LibraryBacktestBox champ={champ} />;
  return <LegacyBacktestBox champ={champ} />;
}
