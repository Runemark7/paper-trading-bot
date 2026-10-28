import { Link } from "react-router-dom";
import type { TradeEvent } from "../api/types";
import { championDetailPath } from "../status/championPath";
import { MonoName } from "./ui";
import { shortAgo, usd, utcStamp } from "./kit";

function reasonLabel(e: TradeEvent): string {
  if (e.kind === "entry") return e.open ? "Entry signal · still open" : "Entry signal";
  switch (e.reason) {
    case "take_profit":
      return "Take-profit hit";
    case "stop_loss":
      return "Stop-loss hit";
    case "manual":
      return "Manual close";
    default:
      return e.reason ?? "—";
  }
}

function ActionPill({ e }: { e: TradeEvent }) {
  const buy = e.kind === "entry";
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${
        buy ? "bg-sky-500/15 text-sky-200" : "bg-violet-500/15 text-violet-200"
      }`}
    >
      {buy ? "Buy" : "Sell"}
    </span>
  );
}

function pnlClass(n: number | null | undefined): string {
  if (n == null) return "text-white/40";
  return n >= 0 ? "text-emerald-300" : "text-rose-300";
}

function qty(n: number | null | undefined): string {
  return n == null ? "—" : n.toFixed(4);
}

export default function TradeLog({ events, showChampion = false }: { events: TradeEvent[]; showChampion?: boolean }) {
  return (
    <>
      <ul className="md:hidden space-y-2 min-w-0">
        {events.map((e) => (
          <li key={`${e.champion}-${e.trade_id}-${e.kind}`} className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 min-w-0">
                <ActionPill e={e} />
                <span className="text-sm font-medium text-white">{e.symbol ?? "—"}</span>
                <span className="text-sm tabular-nums text-white/80">@ {usd(e.price)}</span>
              </span>
              <span className={`shrink-0 text-sm font-semibold tabular-nums ${pnlClass(e.pnl)}`}>
                {e.kind === "exit" ? usd(e.pnl, 2, true) : ""}
              </span>
            </div>
            <div className="mt-1 text-xs text-white/50">
              {reasonLabel(e)} · {shortAgo(e.ts)} · {utcStamp(e.ts)}
            </div>
            <div className="mt-1 text-xs text-white/45 tabular-nums">
              size {qty(e.size)} {(e.symbol ?? "").split("/")[0]} · {usd(e.notional)} · fee {usd(e.fee, 2)}
            </div>
            {showChampion ? (
              <Link to={championDetailPath(e.champion)} className="mt-1 block min-w-0 text-xs text-indigo-200/80 hover:text-white">
                <MonoName className="text-[11px]">{e.champion}</MonoName>
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
      <div className="hidden md:block overflow-x-auto min-w-0">
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-white/40">
            <tr className="text-left">
              <th className="py-2 pr-3 font-medium">When</th>
              {showChampion ? <th className="py-2 pr-3 font-medium">Champion</th> : null}
              <th className="py-2 pr-3 font-medium">Action</th>
              <th className="py-2 pr-3 text-right font-medium">Price</th>
              <th className="py-2 pr-3 text-right font-medium">Size</th>
              <th className="py-2 pr-3 text-right font-medium">Value</th>
              <th className="py-2 pr-3 text-right font-medium">Fee</th>
              <th className="py-2 pr-3 text-right font-medium">P&amp;L after fees</th>
              <th className="py-2 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={`${e.champion}-${e.trade_id}-${e.kind}`} className="border-t border-white/[0.06] align-top">
                <td className="py-2 pr-3 whitespace-nowrap">
                  <div className="text-white/85">{shortAgo(e.ts)}</div>
                  <div className="text-[11px] text-white/40">{utcStamp(e.ts)}</div>
                </td>
                {showChampion ? (
                  <td className="py-2 pr-3 max-w-[16rem]">
                    <Link to={championDetailPath(e.champion)} className="hover:text-white text-white/75">
                      <MonoName className="text-[11px]">{e.champion}</MonoName>
                    </Link>
                  </td>
                ) : null}
                <td className="py-2 pr-3 whitespace-nowrap">
                  <ActionPill e={e} /> <span className="text-white/70 text-xs">{e.symbol}</span>
                </td>
                <td className="py-2 pr-3 text-right tabular-nums">{usd(e.price)}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-white/70">{qty(e.size)}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-white/70">{usd(e.notional)}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-white/60">{usd(e.fee, 2)}</td>
                <td className={`py-2 pr-3 text-right tabular-nums font-semibold ${pnlClass(e.pnl)}`}>
                  {e.kind === "exit" ? (
                    <>
                      {usd(e.pnl, 2, true)}
                      {e.pnl_pct != null ? (
                        <div className="text-[11px] font-normal text-white/40">
                          price {e.pnl_pct >= 0 ? "+" : ""}
                          {(e.pnl_pct * 100).toFixed(2)}%
                        </div>
                      ) : null}
                    </>
                  ) : (
                    <span className="text-white/30">—</span>
                  )}
                </td>
                <td className="py-2 text-white/65 text-xs">{reasonLabel(e)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
