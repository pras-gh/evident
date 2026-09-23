import type {
  CardDetail,
  CitationRef,
  DocumentView,
  EntityDetail,
  MemoryCard,
  MemorySummary,
  ResolvedCitation,
  TimelineEntry,
} from "./types";

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

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    // resolution depends on what is extracted right now, so never cache it
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

export const getEntity = (t: string, slug: string) =>
  get<EntityDetail>(`/companies/${t}/entities/${encodeURIComponent(slug)}`);

export const getDocumentPages = (documentId: number) =>
  get<DocumentView>(`/documents/${documentId}/pages`, 300);

/** Resolve an answer's citations in one call. Order is preserved and a bad
 *  citation comes back `resolved: false` rather than failing the rest. */
export const resolveCitations = (citations: CitationRef[]) =>
  post<{ citations: ResolvedCitation[] }>("/evidence/resolve", { citations })
    .then((r) => r.citations);
