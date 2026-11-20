import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EvidenceViewer } from "@/components/evidence/evidence-viewer";
import {
  getCompanyTimeline,
  getDocument,
  getDocumentPages,
  getEntity,
  getEvidence,
  resolveCitations,
} from "@/lib/api";
import { parseCite } from "@/lib/cite";
import type {
  CitationRef,
  DocumentDetail,
  DocumentView,
  EntityMention,
  EvidenceEntity,
  ResolvedCitation,
} from "@/lib/types";

type Params = Promise<{ chunkId: string }>;
type Search = Promise<{ cite?: string | string[]; entity?: string | string[] }>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) || undefined;

/** The citation to open with: `?cite=` if given, else the chunk in the path. */
function opening(chunkId: string, cite: string | undefined) {
  return parseCite(cite) ?? parseCite(chunkId);
}

export async function generateMetadata({ params, searchParams }: {
  params: Params;
  searchParams: Search;
}): Promise<Metadata> {
  const { chunkId } = await params;
  const sp = await searchParams;
  const cite = opening(chunkId, one(sp.cite));
  const ev = cite ? await getEvidence(cite.chunk_id, cite.paragraph_id, one(sp.entity)).catch(() => null) : null;
  const name = ev?.entities.find((e) => e.slug === one(sp.entity))?.name ?? ev?.entities[0]?.name;
  return { title: `${name ?? "Evidence"}${ev?.ticker ? ` · ${ev.ticker}` : ""} — Evident` };
}

/** Reading order within the entity's citations: newest filing first, then by
 *  page and paragraph, so the chips read like the filings do. */
function order(a: EntityMention, b: EntityMention): number {
  if (a.observed_at !== b.observed_at) return a.observed_at < b.observed_at ? 1 : -1;
  const pos = (m: EntityMention) =>
    (m.provenance.paragraph_id ?? "").split("#")[0].split("_").map(Number);
  const [pa, ia] = pos(a);
  const [pb, ib] = pos(b);
  return (pa || 0) - (pb || 0) || (ia || 0) - (ib || 0);
}

/**
 * `/evidence/{chunkId}?cite={chunk}:{paragraph}&entity={slug}`
 *
 * Opens the filing at the cited paragraph. With an entity — named, or else
 * the most confident one extracted from the paragraph — every citation of it
 * becomes a chip, and its timeline supplies the claim.
 */
export default async function EvidencePage({ params, searchParams }: {
  params: Params;
  searchParams: Search;
}) {
  const { chunkId } = await params;
  const sp = await searchParams;
  const cite = opening(chunkId, one(sp.cite));
  if (!cite) notFound();

  let first;
  try {
    first = await getEvidence(cite.chunk_id, cite.paragraph_id, one(sp.entity));
  } catch {
    notFound();
  }

  const slug = one(sp.entity) ?? first.entities[0]?.slug ?? null;
  const ticker = first.ticker;
  let entity: EvidenceEntity | null = first.entities.find((e) => e.slug === slug) ?? null;
  let citations: ResolvedCitation[] = [];
  let claims: Awaited<ReturnType<typeof getCompanyTimeline>>["events"] = [];

  if (slug && ticker) {
    const [detail, timeline] = await Promise.all([
      getEntity(ticker, slug).catch(() => null),
      getCompanyTimeline(ticker, undefined, slug).catch(() => null),
    ]);
    if (detail) {
      entity ??= { slug, name: detail.name, entity_type: detail.entity_type,
                   confidence: null, highlight_color: first.highlight_color };
      const refs: CitationRef[] = detail.mentions
        .filter((m) => m.provenance.chunk_id != null)
        .sort(order)
        .map((m) => ({ chunk_id: m.provenance.chunk_id as number,
                       paragraph_id: m.provenance.paragraph_id, entity: slug }));
      citations = refs.length ? await resolveCitations(refs) : [];
    }
    claims = timeline?.events ?? [];
  }

  // Open on the requested citation: the same paragraph, or — when only a
  // chunk was named — the entity's first citation in that chunk.
  let initial = citations.findIndex(
    (c) => c.evidence?.chunk_id === first.chunk_id &&
      (cite.paragraph_id == null || c.evidence.paragraph_id === first.paragraph_id),
  );
  if (initial < 0) {
    citations = [{ index: 0, resolved: true, error: null, evidence: first }, ...citations];
    initial = 0;
  }

  const ids = [...new Set(citations.flatMap((c) => (c.evidence ? [c.evidence.document_id] : [])))];
  const documents: Record<number, DocumentDetail> = Object.fromEntries(
    await Promise.all(ids.map(async (id) => [id, await getDocument(id)] as const)),
  );
  // Filings not rendered yet are shown as text.
  const texts: Record<number, DocumentView> = Object.fromEntries(
    await Promise.all(
      ids.filter((id) => !documents[id].pages.some((p) => p.image_url))
        .map(async (id) => [id, await getDocumentPages(id)] as const),
    ),
  );

  return (
    <EvidenceViewer entity={entity} claims={claims} citations={citations}
      initialActive={initial} documents={documents} texts={texts} />
  );
}
