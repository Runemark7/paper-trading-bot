import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, fetchChampionTrades, fetchChampions, fetchDiscoverySummary } from "../api/client";
import { pollInterval } from "../api/poll";
import type { DiscoverySummary } from "../api/types";
import { Card, Stat, fmt, fmtPct, Badge, Section, Empty, MonoName, Field, FieldGrid, PhoneCards, DesktopTable } from "../components/ui";
import {
  AreaLink,
  BlockChips,
  Disclosure,
  EmptyState,
  ErrorNote,
  InfoTip,
  Kpi,
  Panel,
  SectionTitle,
  SkeletonRows,
  StatusDot,
  shortAgo,
  toneOf,
  usd,
} from "../components/kit";
import TradeLog from "../components/TradeLog";
import { FORWARD_CAP_DEFAULT } from "../lib/gates";
import { championDetailPath } from "../status/championPath";
import { farmStatusLabel } from "../status/FarmControl";
import {
  accountLabel,
  cadenceLabel,
  certaintyLabel,
  fmtChampionSince,
  fmtWhen,
  openLotsTotal,
  positionEntry,
  positionPnlPct,
} from "../status/format";

const JOURNEY = [
  { step: "Generate", text: "Randomly combine 2–7 building blocks into an entry rule." },
  { step: "Backtest", text: "18 walk-forward windows of 5-minute BTC data, with fees." },
  { step: "Gate v5", text: "8 strict checks against luck, overfitting and costs." },
  { step: "Propose", text: "Passers are ranked; the top 5 are proposed." },
  { step: "Admit", text: "The owner hand-picks up to 5 champions." },
  { step: "Forward test", text: "Paper-trade live Binance prices. Win = profit after fees." },
];

export default function Overview() {
  const disc = useQuery<DiscoverySummary, Error>({
    queryKey: ["discovery-summary", "compact"],
    queryFn: () => fetchDiscoverySummary({ compact: true }),
    refetchInterval: pollInterval(30_000),
  });
  const champs = useQuery({ queryKey: ["champions"], queryFn: fetchChampions });
  const trades = useQuery({
    queryKey: ["champion-trades", "all"],
    queryFn: () => fetchChampionTrades({ limit: 50 }),
  });

  const counts = disc.data?.counts;
  const tested = counts?.unique_tested ?? counts?.tested;
  const passed = counts?.tested_pass;
  const active = champs.data?.active_champions ?? [];
  const cap = champs.data?.forward_candidates?.cap ?? champs.data?.forward_champion_cap ?? FORWARD_CAP_DEFAULT;
  const proposed = champs.data?.forward_candidates?.proposed?.length ?? 0;
  const summaries = trades.data?.champions ?? [];
  const forwardPnl = summaries.length
    ? summaries.reduce((sum, c) => sum + (c.equity != null ? c.equity - c.start_cash : c.realized_pnl), 0)
    : null;
  const closedTotal = summaries.reduce((n, c) => n + c.closed, 0);
  const farm = disc.data?.farm;
  const farmTone = farm?.status === "running" ? "pos" : farm?.status === "worker_unseen" ? "warn" : "idle";
  const byName = new Map(summaries.map((c) => [c.name, c]));

  return (
    <div className="space-y-8 min-w-0">
      <section className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[radial-gradient(ellipse_at_top_left,rgba(99,102,241,0.22),transparent_55%),radial-gradient(ellipse_at_bottom_right,rgba(245,158,11,0.14),transparent_55%)] p-5 sm:p-8">
        <div className="max-w-3xl">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-sky-500/15 px-2.5 py-1 font-semibold text-sky-200 ring-1 ring-sky-400/30">Paper money only</span>
            <span className="rounded-full bg-white/10 px-2.5 py-1 text-white/70">BTC/USDT · 5-minute bars</span>
          </div>
          <h1 className="mt-4 text-[26px] leading-tight sm:text-4xl font-semibold tracking-tight text-white">
            Searching for a Bitcoin trading strategy that survives honest testing.
          </h1>
          <p className="mt-3 text-[15px] sm:text-base leading-relaxed text-white/70">
            PaperBot is a research bot. A farm of workers invents strategies by randomly combining simple
            building blocks, then tries hard to prove each one is a fluke. The rare survivors become{" "}
            <span className="text-amber-200">champions</span> and trade live Binance prices — with simulated
            money, never real funds.
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <AreaLink to="/discovery">See the search</AreaLink>
            <AreaLink to="/champions">See champions &amp; trades</AreaLink>
          </div>
        </div>
      </section>

      <section aria-label="Current state" className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi
          label="Strategies tested"
          value={tested != null ? tested.toLocaleString("en-US") : "—"}
          loading={disc.isPending}
          hint={counts?.evals_today != null ? `${counts.evals_today.toLocaleString("en-US")} tested today` : undefined}
          info={<InfoTip label="Strategies tested">Unique strategy combinations that finished a full walk-forward backtest. Each is tested once; a fail is final.</InfoTip>}
        />
        <Kpi
          label="Passed the gate"
          value={passed != null ? passed.toLocaleString("en-US") : "—"}
          loading={disc.isPending}
          tone="accent"
          hint={tested && passed != null ? `${((passed / tested) * 100).toFixed(2)}% of tested` : undefined}
          info={<InfoTip label="Passed the gate">Cleared every gate v5 check: trades, Sharpe, baseline, sealed holdout, deflated Sharpe, overfitting, robustness and cost stress.</InfoTip>}
        />
        <Kpi
          label="Active champions"
          value={champs.data ? `${active.length} / ${cap}` : "—"}
          loading={champs.isPending}
          hint={champs.data ? (proposed ? `${proposed} proposed, awaiting approval` : "none awaiting approval") : undefined}
          info={<InfoTip label="Active champions">Passing strategies the owner admitted for forward testing. At most {cap} at a time.</InfoTip>}
        />
        <Kpi
          label="Forward P&L after fees"
          value={usd(forwardPnl, 2, true)}
          tone={toneOf(forwardPnl)}
          loading={trades.isPending}
          hint={trades.data ? `${closedTotal} closed trade${closedTotal === 1 ? "" : "s"} · paper` : undefined}
          info={<InfoTip label="Forward P&L after fees">Sum over active champions of account equity minus the $10,000 starting cash, from each account&apos;s last live-cycle snapshot. Fees and slippage are already deducted.</InfoTip>}
        />
      </section>
      {disc.isError || champs.isError || trades.isError ? (
        <ErrorNote
          what={[disc.isError && "discovery counts", champs.isError && "champions", trades.isError && "champion trades"].filter(Boolean).join(", ")}
          error={disc.error ?? champs.error ?? trades.error}
          stale={Boolean(disc.data || champs.data || trades.data)}
        />
      ) : null}

      <section className="grid gap-4 lg:grid-cols-2">
        <Panel className="flex flex-col">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-indigo-300">Part 1 · Discovery</div>
          <h2 className="mt-1 text-xl font-semibold text-white">The search</h2>
          <p className="mt-2 text-sm leading-relaxed text-white/65">
            Workers draw 2–7 building blocks (trend, momentum, dips, RSI, breakouts, higher-timeframe filters),
            backtest the combination walk-forward, and run it through gate v5. Almost everything fails — that is the point.
          </p>
          <div className="mt-4 grid grid-cols-3 gap-2 text-center">
            {[
              { label: "tested", value: tested },
              { label: "passed", value: passed },
              { label: "admitted", value: champs.data ? active.length : undefined },
            ].map((x) => (
              <div key={x.label} className="rounded-lg bg-white/[0.04] px-2 py-2.5">
                <div className="text-lg font-semibold tabular-nums text-white">{x.value != null ? x.value.toLocaleString("en-US") : "—"}</div>
                <div className="text-[11px] uppercase tracking-wider text-white/45">{x.label}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-2 text-xs text-white/55">
            <StatusDot tone={farmTone} />
            Worker farm: {disc.data ? farmStatusLabel(farm) : "…"}
            {disc.data?.last_tested_at ? ` · last test ${shortAgo(disc.data.last_tested_at)}` : ""}
          </div>
          <div className="mt-auto pt-4">
            <Link
              to="/discovery"
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-indigo-500/90 px-3.5 py-2 text-sm font-medium text-white hover:bg-indigo-400"
            >
              Explore discovery <span aria-hidden>→</span>
            </Link>
          </div>
        </Panel>

        <Panel className="flex flex-col">
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-amber-300">Part 2 · Champions</div>
          <h2 className="mt-1 text-xl font-semibold text-white">The forward test</h2>
          <p className="mt-2 text-sm leading-relaxed text-white/65">
            Admitted strategies trade live prices in their own $10,000 paper account. A champion succeeds only if it is
            profitable after fees.
          </p>
          <ul className="mt-4 space-y-2 min-w-0">
            {champs.isPending ? (
              <SkeletonRows rows={2} />
            ) : !active.length ? (
              <li className="text-sm text-white/50">No champions admitted yet. The book falls back to the sma_stack baseline.</li>
            ) : (
              active.map((c) => {
                const t = byName.get(c.name);
                const pnl = t ? (t.equity != null ? t.equity - t.start_cash : t.realized_pnl) : null;
                return (
                  <li key={c.name}>
                    <Link
                      to={championDetailPath(c.name)}
                      className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.04] px-3 py-2.5 hover:bg-white/[0.07] min-w-0"
                    >
                      <span className="min-w-0">
                        <BlockChips name={c.name} />
                        <span className="mt-1 block text-[11px] text-white/45">
                          {t ? `${t.closed} closed · ${t.open} open` : `${c.closed} closed`} · since {fmtChampionSince(c.champion_since)}
                        </span>
                      </span>
                      <span className={`shrink-0 text-sm font-semibold tabular-nums ${pnl == null ? "text-white/40" : pnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                        {usd(pnl, 2, true)}
                      </span>
                    </Link>
                  </li>
                );
              })
            )}
          </ul>
          <div className="mt-auto pt-4">
            <Link
              to="/champions"
              className="inline-flex min-h-11 items-center gap-1.5 rounded-lg bg-amber-500/90 px-3.5 py-2 text-sm font-medium text-[#1a1204] hover:bg-amber-400"
            >
              See champions &amp; trades <span aria-hidden>→</span>
            </Link>
          </div>
        </Panel>
      </section>

      <section>
        <SectionTitle title="How a strategy becomes a champion" />
        <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          {JOURNEY.map((j, i) => (
            <li key={j.step} className="rounded-xl border border-white/[0.08] bg-white/[0.025] p-3">
              <div className="flex items-center gap-2">
                <span className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${i < 3 ? "bg-indigo-500/25 text-indigo-200" : "bg-amber-500/25 text-amber-200"}`}>
                  {i + 1}
                </span>
                <span className="text-sm font-semibold text-white">{j.step}</span>
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-white/55">{j.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <SectionTitle title="Latest champion trades" aside={<Link to="/champions" className="hover:text-white">Full trade log →</Link>}>
          Every entry and exit across all champions, newest first. P&amp;L is after fees.
        </SectionTitle>
        {trades.isPending ? (
          <SkeletonRows rows={3} />
        ) : !(trades.data?.events ?? []).length ? (
          <EmptyState title="No champion trades yet">
            Champions only enter when every one of their rules is true on the same closed 5-minute bar. That can take days.
            The live cycle checks every 5 minutes.
          </EmptyState>
        ) : (
          <Panel className="p-3 sm:p-4">
            <TradeLog events={(trades.data?.events ?? []).slice(0, 6)} showChampion />
          </Panel>
        )}
      </section>

      <Disclosure
        title="System detail"
        summary="Live paper book, cycle timing, heartbeat, open lots and pipeline stamps — for the operator."
      >
        <SystemDetail />
      </Disclosure>
    </div>
  );
}

function SystemDetail() {
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
  const openLots = openLotsTotal(l, run?.open_lots ?? run?.positions_open);

  return (
    <div className="space-y-6 min-w-0">
      <p className="text-sm text-white/55">
        Paper only. What is on the live paper book versus last-known pipeline work.
        Spinners appear only from a sidecar stamp — never invented.
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
          <Badge tone={openLots ? "pos" : "neutral"}>
            {run ? `${openLots} open lots` : "open lots …"}
          </Badge>
          <Badge tone={hb?.last_pass_at ? (hb.recent ? "pos" : "neutral") : "warn"}>
            heartbeat: {hb?.last_pass_at ? (hb.recent ? "recent stamp" : "last-known stamp") : "no stamp"}
          </Badge>
          <Badge tone="neutral">
            cycle {cycle ? cadenceLabel(cycle.interval_seconds) : "…"} · {cycle?.bar_timeframe ?? "5m"} bars
          </Badge>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm mb-4">
          <div>
            <div className="text-white/40 text-xs uppercase tracking-wider mb-1">Active on the book</div>
            <div className="font-mono text-white min-w-0 space-y-1">
              {(run?.strategy.active ?? []).length
                ? (run?.strategy.active ?? []).map((n) => (
                    <Link key={n} to={championDetailPath(n)} className="block min-w-0 hover:text-white">
                      <MonoName className="block text-sm">{n}</MonoName>
                    </Link>
                  ))
                : "—"}
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
              Sidecar {cycle?.window ?? "24/7"}. {cycle?.next.note}
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

        <Card title={`Open lots on the paper book (${openLots})`} aside={l?.as_of ? `prices ${l.as_of}` : undefined}>
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
                    <li key={i} className="rounded-lg border border-white/10 p-3 space-y-2 min-w-0 overflow-hidden">
                      <div className="flex items-baseline justify-between gap-2 min-w-0">
                        <span className="font-medium min-w-0 truncate">{p.symbol}</span>
                        <span className={`shrink-0 ${(pnlPct ?? 0) >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                          {fmtPct(pnlPct)}
                        </span>
                      </div>
                      <MonoName className="block text-xs text-white/50">{accountLabel(p.account)}</MonoName>
                      <FieldGrid>
                        <Field label="Condition" span mono>
                          {p.condition ?? "—"}
                        </Field>
                        <Field label="Entry">{fmt(positionEntry(p))}</Field>
                        <Field label="Now">{p.current != null ? fmt(p.current) : "—"}</Field>
                      </FieldGrid>
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
                          <td className="py-2">
                            <MonoName className="text-xs">{accountLabel(p.account)}</MonoName>
                          </td>
                          <td className="font-medium">{p.symbol}</td>
                          <td className="text-white/60 font-mono text-xs break-all">{p.condition ?? "—"}</td>
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
                to="/discovery"
                chip={
                  prog.pipeline.stale && prog.pipeline.phase === "tournament"
                    ? "discovery stuck / cycle overdue"
                    : prog.discovery.last_tested_at
                      ? `last ${fmtWhen(prog.discovery.last_tested_at)}`
                      : "no log"
                }
                tone={
                  prog.pipeline.stale && prog.pipeline.phase === "tournament"
                    ? "warn"
                    : "neutral"
                }
                body={
                  prog.pipeline.stale && prog.pipeline.phase === "tournament"
                    ? `Tournament stamp started with no finish. ${prog.discovery.note ?? ""}`
                    : prog.discovery.last_strategy
                    ? `${prog.discovery.last_strategy} · ${prog.discovery.last_qualified ? "qualified" : "rejected"} · ${prog.discovery.unique_tested ?? "?"} unique / ${prog.discovery.log_count} log rows. ${prog.discovery.note ?? ""}`
                    : (prog.discovery.note ?? "No discovery log yet.")
                }
              />
              <PipelineRow
                title="Replenish"
                chip={
                  prog.replenish.stamp_says_this_phase
                    ? "stamp says tournament/replenish"
                    : prog.replenish.needed
                      ? "untested names remain"
                      : "universe exhausted"
                }
                tone={prog.replenish.stamp_says_this_phase ? "wait" : "neutral"}
                body={prog.replenish.note ?? "No live-slot cap. needed is leftover universe names, not slots under 20."}
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
  to,
}: {
  title: string;
  chip: string;
  tone: "wait" | "warn" | "neutral";
  body: string;
  to?: string;
}) {
  return (
    <div className="min-w-0 rounded-lg border border-white/10 p-3">
      <div className="flex flex-col gap-1.5 mb-1 sm:flex-row sm:items-start sm:justify-between sm:gap-2">
        {to ? (
          <Link to={to} className="font-medium text-white hover:underline">
            {title}
          </Link>
        ) : (
          <div className="font-medium text-white">{title}</div>
        )}
        <Badge tone={tone}>{chip}</Badge>
      </div>
      <div className="text-white/50 text-xs leading-relaxed break-words [overflow-wrap:anywhere]">{body}</div>
    </div>
  );
}
