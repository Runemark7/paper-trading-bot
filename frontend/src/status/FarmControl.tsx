import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchDiscoverySummary, setDiscoveryFarm } from "../api/client";
import type { DiscoveryFarm } from "../api/types";
import { Badge, Card } from "../components/ui";
import { fmtWhen } from "./format";

const TOKEN_KEY = "paper_discovery_farm_token";

function loadToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

function saveToken(token: string) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode */
  }
}

export function farmStatusLabel(farm?: DiscoveryFarm | null): string {
  switch (farm?.status) {
    case "running":
      return "Running";
    case "paused":
      return "Paused";
    case "worker_idle":
      return "Worker idle";
    case "worker_unseen":
      return "Worker not seen";
    default:
      return farm?.enabled === false ? "Paused" : "farm status unknown";
  }
}

function farmTone(status?: string): "pos" | "neg" | "warn" | "wait" | "neutral" {
  switch (status) {
    case "running":
      return "pos";
    case "paused":
      return "wait";
    case "worker_idle":
      return "neutral";
    case "worker_unseen":
      return "warn";
    default:
      return "neutral";
  }
}

/** Prominent Start/Stop for the Windows discovery farm. Token stays in this tab. */
export default function FarmControl() {
  const queryClient = useQueryClient();
  const q = useQuery({
    queryKey: ["discovery-summary"],
    queryFn: fetchDiscoverySummary,
    refetchInterval: 15_000,
  });
  const [savedToken, setSavedToken] = useState(loadToken);
  const [tokenDraft, setTokenDraft] = useState("");
  const farm = q.data?.farm;
  const enabled = farm?.enabled !== false;
  const unseen = farm?.status === "worker_unseen" || farm?.worker_seen === false;
  const tokenReady = Boolean((savedToken || tokenDraft).trim());

  const mutate = useMutation({
    mutationFn: (next: boolean) => {
      const token = (savedToken || tokenDraft).trim();
      if (!token) {
        throw new Error("Paste the paper discovery ingest token to Start or Stop.");
      }
      return setDiscoveryFarm(next, token);
    },
    onSuccess: (_data, _next, _ctx) => {
      const incoming = tokenDraft.trim();
      if (incoming) {
        saveToken(incoming);
        setSavedToken(incoming);
        setTokenDraft("");
      }
      void queryClient.invalidateQueries({ queryKey: ["discovery-summary"] });
    },
  });

  const busy = mutate.isPending;
  const actionLabel = enabled ? "Stop discovery" : "Start discovery";

  return (
    <Card title="Discovery farm" aside="jensa · Windows worker">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <Badge tone={farmTone(farm?.status)}>{farmStatusLabel(farm)}</Badge>
        <span className="text-xs text-white/50">
          last heartbeat {farm?.heartbeat_at ? fmtWhen(farm.heartbeat_at) : "never recorded"}
        </span>
      </div>

      <p className="text-sm text-white/65 mb-3">
        Start / Stop is authenticated. Anyone who can open this site cannot pause
        the farm — paste the paper ingest token first. It stays in this browser
        tab only (`sessionStorage`) and is never shipped in the JS bundle.
        Leave the worker running on jensa. Stop idles it (near-zero CPU) so you can
        game. Start resumes from this page — it does not kill or relaunch the
        process. A batch already running may finish first.
      </p>

      {unseen && (
        <div className="mb-3 rounded-md border border-amber-400/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
          Worker not seen — Start will not relaunch a dead process. On jensa run{" "}
          <code className="text-amber-50">python scripts/discovery_worker.py --workers 2</code>
          .
        </div>
      )}

      {farm?.note && !unseen ? (
        <p className="text-xs text-white/45 mb-3">{farm.note}</p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <button
          type="button"
          disabled={busy || !tokenReady}
          onClick={() => mutate.mutate(!enabled)}
          className={`min-h-11 px-4 py-2 rounded text-sm font-medium ${
            enabled
              ? "bg-amber-500/90 hover:bg-amber-400 text-[#1a1204]"
              : "bg-emerald-500/90 hover:bg-emerald-400 text-[#04140c]"
          } disabled:opacity-50`}
        >
          {busy ? "Updating…" : actionLabel}
        </button>
      </div>

      <label className="block min-w-0 text-xs text-white/50">
        <span className="uppercase tracking-wider text-white/40">Farm token</span>
        {savedToken ? (
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="text-white/60">Saved in this tab (same as the ingest token).</span>
            <button
              type="button"
              onClick={() => {
                saveToken("");
                setSavedToken("");
              }}
              className="min-h-11 px-3 py-1.5 rounded bg-white/10 hover:bg-white/20 text-white"
            >
              Clear token
            </button>
          </div>
        ) : (
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={tokenDraft}
            onChange={(e) => setTokenDraft(e.target.value)}
            placeholder="paste ingest token"
            aria-label="Farm token"
            className="mt-1 w-full max-w-md min-h-11 rounded-md bg-[#121a38] px-2 text-xs text-white ring-1 ring-white/15 placeholder:text-white/30"
          />
        )}
      </label>

      {mutate.isError && (
        <p className="mt-3 text-sm text-rose-300">{String(mutate.error)}</p>
      )}
    </Card>
  );
}
