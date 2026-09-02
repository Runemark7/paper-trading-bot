import type {
  Summary,
  LivePreview,
  RegimeState,
  TradeRow,
  LearningMap,
  GraduatedStrategy,
  DiscoveryEvaluation,
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

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json() as Promise<T>;
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
  status: () => get<StatusSnapshot>("/api/status"),
  health: () => get<{ ok: boolean }>("/healthz"),
};

// Champion pool is served live; wrapped here so UI can poll it.
export async function fetchChampions(): Promise<ChampionsPayload> {
  const res = await fetch("/api/champions");
  if (!res.ok) throw new Error(`/api/champions -> ${res.status}`);
  return res.json();
}

export async function fetchGraduated(): Promise<GraduatedStrategy[]> {
  const res = await fetch("/api/graduated");
  if (!res.ok) throw new Error(`/api/graduated -> ${res.status}`);
  return res.json();
}

export async function fetchDiscovery(): Promise<DiscoveryEvaluation[]> {
  const res = await fetch("/api/discovery");
  if (!res.ok) throw new Error(`/api/discovery -> ${res.status}`);
  return res.json();
}
