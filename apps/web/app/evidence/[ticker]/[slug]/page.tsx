import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EvidenceViewer } from "@/components/evidence/EvidenceViewer";
import { getDocumentPages, getEntity, resolveCitations } from "@/lib/api";
import type { Answer, DocumentView } from "@/lib/types";

export const revalidate = 60;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ ticker: string; slug: string }>;
}): Promise<Metadata> {
  const { ticker, slug } = await params;
  // the same request the page makes, so Next serves it from its fetch cache
  const entity = await getEntity(ticker, slug).catch(() => null);
  return {
    title: entity
      ? `${entity.name} — Evidence · ${ticker.toUpperCase()}`
      : `Evidence · ${ticker.toUpperCase()}`,
  };
}

/**
 * An entity's evidence, in the viewer.
 *
 * There is no answer endpoint yet, so the entity stands in as the claim and its
 * mentions are the citations. The viewer does not know the difference — when
 * answers exist, they render through the same component unchanged.
 */
export default async function EntityEvidencePage({
  params,
}: {
  params: Promise<{ ticker: string; slug: string }>;
}) {
  const { ticker, slug } = await params;

  let entity;
  try {
    entity = await getEntity(ticker, slug);
  } catch {
    notFound();
  }

  // A mention without a chunk cannot be located, so it cannot be a citation.
  const refs = entity.mentions
    .filter((m) => m.provenance.chunk_id != null)
    .map((m) => ({
      chunk_id: m.provenance.chunk_id as number,
      paragraph_id: m.provenance.paragraph_id,
      entity: entity.slug,
    }));

  const citations = refs.length ? await resolveCitations(refs) : [];

  const documentIds = [
    ...new Set(citations.flatMap((c) => (c.evidence ? [c.evidence.document_id] : []))),
  ];
  const documents: Record<number, DocumentView> = Object.fromEntries(
    await Promise.all(
      documentIds.map(async (id) => [id, await getDocumentPages(id)] as const),
    ),
  );

  const answer: Answer = {
    text:
      entity.description ??
      `${entity.name} is recorded as a ${entity.entity_type}, cited in ${citations.length} ` +
        `place${citations.length === 1 ? "" : "s"} across ${documentIds.length} ` +
        `filing${documentIds.length === 1 ? "" : "s"}.`,
    citations,
  };

  return (
    <EvidenceViewer
      kicker={`${ticker.toUpperCase()} · ${entity.entity_type}`}
      title={entity.name}
      answer={answer}
      documents={documents}
    />
  );
}
