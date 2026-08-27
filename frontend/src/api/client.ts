import type {
  Summary,
  LivePreview,
  RegimeState,
  TradeRow,
  LearningMap,
  Champion,
  GraduatedStrategy,
  DiscoveryEvaluation,
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
  trades: () => get<TradeRow[]>("/api/trades"),
  learning: () => get<LearningMap>("/api/learning"),
  graduated: () => get<GraduatedStrategy[]>("/api/graduated"),
  discovery: () => get<DiscoveryEvaluation[]>("/api/discovery"),
  health: () => get<{ ok: boolean }>("/healthz"),
};

// Champion pool is served live; wrapped here so UI can poll it.
export async function fetchChampions(): Promise<{
  active_champions: Champion[];
  active_count: number;
  target_active: number;
  graduated_count: number;
  graduated: GraduatedStrategy[];
}> {
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