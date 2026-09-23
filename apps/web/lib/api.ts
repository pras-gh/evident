import type {
  CardDetail,
  CitationRef,
  CompanyListItem,
  CompanyTimeline,
  DocumentSummary,
  DocumentView,
  EntityDetail,
  MemoryCard,
  MemorySummary,
  PageView,
  ResolvedCitation,
} from "./types";

const BASE = process.env.API_URL ?? "http://localhost:8000/v1";

async function get<T>(path: string, revalidate = 60): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { next: { revalidate } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

export const getCompanies = () => get<CompanyListItem[]>(`/companies`);
export const getCompany   = (t: string) => get<MemorySummary>(`/companies/${t}`);
export const getCards     = (t: string) => get<MemoryCard[]>(`/companies/${t}/cards`);
export const getCard      = (t: string, kind: string) =>
  get<CardDetail>(`/companies/${t}/cards/${kind}`);

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

/** A company's filings, newest first, with what each supports. */
export const getCompanyDocuments = (t: string) =>
  get<{ ticker: string | null; company: string; documents: DocumentSummary[] }>(
    `/company/${encodeURIComponent(t)}/documents`,
  );

/** One page of a filing: its paragraphs, anchors and — for PDFs — boxes. */
export const getDocumentPage = (documentId: number, page: number) =>
  get<PageView>(`/document/${documentId}/page/${page}`, 300);

/** The most citations POST /v1/evidence/resolve accepts in one call —
 *  MAX_RESOLVE in apps/api/schemas.py. */
export const RESOLVE_BATCH = 1000;

/** Resolve an answer's citations, in as many calls as the API's per-request
 *  limit needs, in parallel. Order is preserved, and a bad citation comes
 *  back `resolved: false` rather than failing the rest. */
export async function resolveCitations(
  citations: CitationRef[],
  post_ = post,
): Promise<ResolvedCitation[]> {
  const batches: CitationRef[][] = [];
  for (let i = 0; i < citations.length; i += RESOLVE_BATCH) {
    batches.push(citations.slice(i, i + RESOLVE_BATCH));
  }
  const results = await Promise.all(
    batches.map((batch) =>
      post_<{ citations: ResolvedCitation[] }>("/evidence/resolve", { citations: batch }),
    ),
  );
  // each batch numbers from 0; renumber so `index` is the position in the whole
  return results.flatMap((r, b) =>
    r.citations.map((c) => ({ ...c, index: c.index + b * RESOLVE_BATCH })),
  );
}

/** What changed between a company's filings. Filtering by category happens in
 *  the API so the facet counts stay honest. */
export const getCompanyTimeline = (t: string, category?: string) => {
  const q = new URLSearchParams({ limit: "500" });
  if (category) q.set("category", category);
  return get<CompanyTimeline>(`/company/${encodeURIComponent(t)}/timeline?${q}`);
};
