import { pollInterval, SUMMARY_POLL_MS } from "../api/poll";
import { useQuery } from "@tanstack/react-query";
import { fetchChampions, fetchDiscoveryInsights, fetchDiscoverySummary } from "../api/client";
import type { DiscoverySummary } from "../api/types";
import {
  Disclosure,
  ErrorNote,
  Funnel,
  InfoTip,
  PageHeader,
  Panel,
  SectionTitle,
  StatusDot,
  shortAgo,
} from "../components/kit";
import { Badge } from "../components/ui";
import { BLOCK_FAMILIES, CATEGORY_LABEL, CATEGORY_TONE } from "../lib/rules";
import { HOLDOUT_SPAN } from "../lib/gates";
import BlockEffects from "../status/BlockEffects";
import DiscoveryBuckets from "../status/DiscoveryBuckets";
import { GateLegend, NearMisses, RecentTests } from "../status/DiscoveryInsights";
import FarmControl, { farmStatusLabel } from "../status/FarmControl";

function WhatIsSearched({ summary }: { summary?: DiscoverySummary }) {
  const facts = [
    { k: "Combination", v: "2–7 random building blocks; all must be true on the same closed 5-minute bar to enter." },
    { k: "Data", v: "BTC/USDT 5-minute candles from Binance, back to 2021." },
    { k: "Walk-forward", v: "18 out-of-sample windows of 87 days, each preceded by its own training span. No peeking ahead." },
    { k: "Sealed holdout", v: `${HOLDOUT_SPAN}. Never used to generate or pick strategies.` },
    { k: "Same as live", v: "Identical exits (2×ATR stop, 2:1 take-profit), sizing, 0.1% fees and 2 bps slippage." },
  ];
  const queue = summary?.counts.eligible ?? summary?.counts.untested;
  return (
    <Panel>
      <SectionTitle title="What is being searched">Long-only BTC entry rules built from simple, well-known indicators.</SectionTitle>
      <ul className="grid gap-2 sm:grid-cols-2">
        {BLOCK_FAMILIES.map((f) => (
          <li key={f.category} className="rounded-lg bg-white/[0.03] p-2.5 min-w-0">
            <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ${CATEGORY_TONE[f.category]}`}>
              {CATEGORY_LABEL[f.category]}
            </span>
            <p className="mt-1 text-xs leading-relaxed text-white/65">{f.plain}</p>
            <p className="mt-0.5 font-mono text-[10px] text-white/35 break-all">{f.examples}</p>
          </li>
        ))}
      </ul>
      <dl className="mt-4 space-y-2 text-sm">
        {facts.map((f) => (
          <div key={f.k} className="grid grid-cols-[7.5rem_1fr] gap-2">
            <dt className="text-xs font-semibold uppercase tracking-wider text-white/40 pt-0.5">{f.k}</dt>
            <dd className="text-white/70 leading-relaxed">{f.v}</dd>
          </div>
        ))}
      </dl>
      {queue != null ? (
        <p className="mt-3 text-xs text-white/45">
          {queue.toLocaleString("en-US")} untested combinations are queued; the queue refills itself automatically.
        </p>
      ) : null}
    </Panel>
  );
}

function FarmHealth({ summary, loading }: { summary?: DiscoverySummary; loading: boolean }) {
  const farm = summary?.farm;
  const workers = summary?.workers ?? [];
  const live = workers.filter((w) => w.seen);
  const stuck = Boolean(summary?.stuck || summary?.in_flight?.stale);
  const tone = farm?.status === "running" ? "pos" : farm?.status === "worker_unseen" ? "warn" : "idle";
  return (
    <Panel>
      <SectionTitle
        title="Worker farm"
        info={
          <InfoTip label="Worker farm" align="right">
            Home PCs run the discovery worker. Each one leases untested names from this server, backtests them, and posts results back. The server never runs backtests itself.
          </InfoTip>
        }
      >
        Machines that run the backtests.
      </SectionTitle>
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-2 rounded-full bg-white/[0.05] px-3 py-1.5 text-sm text-white">
          <StatusDot tone={tone} />
          {loading ? "…" : farmStatusLabel(farm)}
        </span>
        <span className="text-xs text-white/50">
          heartbeat {farm?.heartbeat_at ? shortAgo(farm.heartbeat_at) : "never"}
        </span>
      </div>
      {stuck ? (
        <div className="mt-3 rounded-lg border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          {summary?.stuck_reason || summary?.in_flight?.note || "Discovery looks stuck — no new results recently."}
        </div>
      ) : null}
      <div className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-white/[0.04] py-2">
          <div className="text-lg font-semibold tabular-nums text-white">{loading ? "…" : `${live.length}/${workers.length}`}</div>
          <div className="text-[10px] uppercase tracking-wider text-white/45">workers online</div>
        </div>
        <div className="rounded-lg bg-white/[0.04] py-2">
          <div className="text-lg font-semibold tabular-nums text-white">{summary?.counts.leased ?? summary?.leases?.active ?? "…"}</div>
          <div className="text-[10px] uppercase tracking-wider text-white/45">testing now</div>
        </div>
        <div className="rounded-lg bg-white/[0.04] py-2">
          <div className="text-lg font-semibold tabular-nums text-white">{summary?.counts.evals_today?.toLocaleString("en-US") ?? "…"}</div>
          <div className="text-[10px] uppercase tracking-wider text-white/45">tested today</div>
        </div>
      </div>
      {workers.length ? (
        <ul className="mt-3 divide-y divide-white/[0.06] text-sm">
          {workers.map((w) => (
            <li key={w.worker_id} className="flex items-center justify-between gap-3 py-2 min-w-0">
              <span className="flex items-center gap-2 min-w-0">
                <StatusDot tone={w.seen ? "pos" : "idle"} />
                <span className="font-mono text-xs text-white/85 truncate">{w.worker_id}</span>
              </span>
              <span className="shrink-0 text-xs text-white/50">
                {w.seen ? `${w.lease_count} leased` : "offline"} · seen {w.last_seen ? shortAgo(w.last_seen) : "never"}
              </span>
            </li>
          ))}
        </ul>
      ) : !loading ? (
        <p className="mt-3 text-sm text-white/50">No worker has checked in yet.</p>
      ) : null}
      <p className="mt-3 text-xs text-white/40">
        Last result {summary?.last_tested_at ? shortAgo(summary.last_tested_at) : "never"}.
      </p>
    </Panel>
  );
}

export default function Discovery() {
  const summary = useQuery<DiscoverySummary, Error>({
    queryKey: ["discovery-summary", "compact"],
    queryFn: () => fetchDiscoverySummary({ compact: true }),
    refetchInterval: pollInterval(SUMMARY_POLL_MS),
    refetchIntervalInBackground: false,
  });
  const insights = useQuery({ queryKey: ["discovery-insights"], queryFn: fetchDiscoveryInsights, refetchInterval: pollInterval(60_000), refetchIntervalInBackground: false });
  const champs = useQuery({ queryKey: ["champions"], queryFn: fetchChampions });

  const counts = summary.data?.counts;
  const tested = counts?.unique_tested ?? counts?.tested;
  const forward = champs.data?.forward_candidates;

  return (
    <div className="space-y-6 min-w-0">
      <PageHeader
        kicker="Part 1 · Discovery"
        title="The strategy search"
        aside={summary.data ? <Badge tone="neutral">updated {shortAgo(summary.data.as_of)}</Badge> : null}
      >
        A worker farm keeps inventing BTC strategies by randomly combining 2–7 building blocks, backtests each one
        walk-forward, and runs it through a strict 8-check gate. Passing strategies are proposed; the owner admits up to
        five as champions for live paper trading.
      </PageHeader>

      {summary.isError ? <ErrorNote what="discovery status" error={summary.error} stale={Boolean(summary.data)} /> : null}
      {insights.isError ? <ErrorNote what="discovery insights" error={insights.error} stale={Boolean(insights.data)} /> : null}

      <section>
        <SectionTitle title="Funnel" aside="every name is tested once; a fail is final">
          From every combination tried to the handful trading live.
        </SectionTitle>
        <Funnel
          steps={[
            { label: "Tested", value: tested, hint: "unique strategies backtested" },
            { label: "Passed gate", value: counts?.tested_pass, hint: "cleared all 8 checks", tone: "pos" },
            {
              label: "Proposed",
              value: forward ? forward.eligible : undefined,
              hint: forward ? `awaiting owner approval · top ${forward.cap} ranked` : "awaiting owner approval",
              tone: "gold",
            },
            {
              label: "Admitted",
              value: champs.data ? champs.data.active_count : undefined,
              hint: `champions trading now · max ${forward?.cap ?? 5}`,
              tone: "gold",
            },
          ]}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <WhatIsSearched summary={summary.data} />
        <FarmHealth summary={summary.data} loading={summary.isPending} />
      </div>

      <GateLegend insights={insights.data} />

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <NearMisses insights={insights.data} loading={insights.isPending} />
        <RecentTests insights={insights.data} loading={insights.isPending} />
      </div>

      <section>
        <SectionTitle title="Which building blocks help?">
          Exploratory: average effect of including a block family, compared with similar strategies without it. Does not change the gate.
        </SectionTitle>
        <BlockEffects />
      </section>

      <Disclosure
        title="Browse every tested strategy"
        summary="Full table with filters and sort, plus the untested queue. Loads the larger GET /api/discovery/summary list only when opened."
      >
        <DiscoveryBuckets />
      </Disclosure>

      <Disclosure title="Owner controls" summary="Start or stop the worker farm. Requires the paper ingest token.">
        <FarmControl />
      </Disclosure>
    </div>
  );
}
