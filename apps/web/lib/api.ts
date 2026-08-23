import type { CardDetail, MemoryCard, MemorySummary, TimelineEntry } from "./types";

const BASE = process.env.API_URL ?? "http://localhost:8000/v1";

async function get<T>(path: string, revalidate = 60): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { next: { revalidate } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

export const getCompany  = (t: string) => get<MemorySummary>(`/companies/${t}`);
export const getCards    = (t: string) => get<MemoryCard[]>(`/companies/${t}/cards`);
export const getCard     = (t: string, kind: string) =>
  get<CardDetail>(`/companies/${t}/cards/${kind}`);
export const getTimeline = (t: string, limit = 8) =>
  get<TimelineEntry[]>(`/companies/${t}/timeline?limit=${limit}`);
