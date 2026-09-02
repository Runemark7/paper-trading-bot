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
  candles: (symbol: ChartSymbol | string, timeframe = "5m", limit = 500) => {
    const q = new URLSearchParams({
      symbol,
      timeframe,
      limit: String(limit),
    });
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
