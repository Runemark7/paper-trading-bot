import type {
  Summary,
  LivePreview,
  RegimeState,
  TradeRow,
  LearningMap,
  GraduatedStrategy,
  DiscoveryEvaluation,
  DiscoveryFarm,
  DiscoverySummary,
  StatusSnapshot,
  ChampionsPayload,
  CandlesPayload,
  ChartSymbol,
} from "./types";

/** 5m live tape. 288 bars/day. Default 3 calendar days; hard cap 7 days. */
export const BARS_PER_DAY = 288;
export const DEFAULT_CANDLE_BARS = 3 * BARS_PER_DAY; // 864
export const MAX_CANDLE_BARS = 7 * BARS_PER_DAY; // 2016
export const ENTRY_PAD_BARS = 12; // 1h of 5m so an entry is not glued to the left edge
export const TF_MS = 5 * 60 * 1000;

/** Bare nginx 502 or our JSON 503/504 — retry once, then show last-known / Empty. */
export function isTransientHttpError(error: unknown): boolean {
  const msg = error instanceof Error ? error.message : String(error ?? "");
  return /-> 502\b|-> 503\b|-> 504\b/.test(msg);
}

async function getOnce<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    let detail = `${path} -> ${res.status}`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) detail = `${detail}: ${body.error}`;
    } catch {
      /* bare nginx 502 is HTML, not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  try {
    return await getOnce<T>(path);
  } catch (err) {
    if (isTransientHttpError(err)) {
      return await getOnce<T>(path);
    }
    throw err;
  }
}

async function postOnce<T>(path: string, body: unknown, token: string): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Discovery-Token": token,
      "X-Paper-Discovery-Token": token,
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${path} -> ${res.status}`;
    try {
      const payload = (await res.json()) as { error?: string };
      if (payload?.error) detail = `${detail}: ${payload.error}`;
    } catch {
      /* bare nginx 502 is HTML, not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export function setDiscoveryFarm(enabled: boolean, token: string) {
  return postOnce<{ ok: boolean; farm: DiscoveryFarm; paper_only?: boolean }>(
    "/api/discovery/farm",
    { enabled, paper_only: true },
    token,
  );
}

export const api = {
  summary: () => get<Summary>("/api/summary"),
  live: () => get<LivePreview>("/api/live"),
  regime: () => get<RegimeState>("/api/regime"),
  trades: (symbol?: string, limit?: number) => {
    const q = new URLSearchParams();
    if (symbol) q.set("symbol", symbol);
    if (limit != null) q.set("limit", String(limit));
    const s = q.toString();
    return get<TradeRow[]>(`/api/trades${s ? `?${s}` : ""}`);
  },
  candles: (
    symbol: ChartSymbol | string,
    timeframe = "5m",
    limit = DEFAULT_CANDLE_BARS,
    sinceMs?: number,
  ) => {
    const q = new URLSearchParams({
      symbol,
      timeframe,
      limit: String(limit),
    });
    if (sinceMs != null && Number.isFinite(sinceMs)) {
      q.set("since", String(Math.floor(sinceMs)));
    }
    return get<CandlesPayload>(`/api/candles?${q.toString()}`);
  },
  learning: () => get<LearningMap>("/api/learning"),
  graduated: () => get<GraduatedStrategy[]>("/api/graduated"),
  discovery: () => get<DiscoveryEvaluation[]>("/api/discovery"),
  discoverySummary: (opts?: { compact?: boolean }) => {
    const q = opts?.compact ? "?compact=1" : "";
    return get<DiscoverySummary>(`/api/discovery/summary${q}`);
  },
  status: () => get<StatusSnapshot>("/api/status"),
  health: () => get<{ ok: boolean }>("/healthz"),
};

// Champion pool is last-known JSON; wrapped here so UI can poll it.
export function fetchChampions(): Promise<ChampionsPayload> {
  return get<ChampionsPayload>("/api/champions");
}

export function fetchGraduated(): Promise<GraduatedStrategy[]> {
  return get<GraduatedStrategy[]>("/api/graduated");
}

export function fetchDiscovery(): Promise<DiscoveryEvaluation[]> {
  return get<DiscoveryEvaluation[]>("/api/discovery");
}

export function fetchDiscoverySummary(opts?: { compact?: boolean }): Promise<DiscoverySummary> {
  return api.discoverySummary(opts);
}
