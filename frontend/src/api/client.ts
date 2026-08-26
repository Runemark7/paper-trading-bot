import type {
  Summary,
  LivePreview,
  RegimeState,
  TradeRow,
  LearningMap,
  Champion,
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
  health: () => get<{ ok: boolean }>("/healthz"),
};

// Champion pool is served live; wrapped here so UI can poll it.
export async function fetchChampions(): Promise<{
  champions: Champion[];
  count: number;
  max: number;
}> {
  const res = await fetch("/api/champions");
  if (!res.ok) throw new Error(`/api/champions -> ${res.status}`);
  return res.json();
}