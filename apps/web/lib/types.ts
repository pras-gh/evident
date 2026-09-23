// Mirrors apps/api/models.py. Evidence is required on anything that makes a
// claim, so a component cannot render an uncited assertion by accident.

export interface Evidence {
  document_id: number;
  accession: string;
  form_type: string;
  page_number: number | null;
  paragraph_id: string | null;
  quote: string;
  section_path: string[];
}

export interface CardFact {
  key: string;
  label: string;
  value: string | null;
  unit: string | null;
  period: string | null;
  status: string | null;
}

export interface CardDelta {
  added: string[];
  removed: string[];
  changed: { label: string; before: string | null; after: string | null }[];
}

export interface CardRevision {
  revision: number;
  as_of: string;
  summary: string;
  source_note: string | null;
  /** false when a filing touched the card without moving anything */
  is_material: boolean;
  facts: CardFact[];
  delta: CardDelta;
  evidence: Evidence[];
}

export interface MemoryCard {
  kind: string;
  title: string;
  /** the "Updates from" binding, e.g. "10-Q / 10-K" */
  source_label: string;
  revision_count: number;
  material_count: number;
  last_updated_at: string | null;
  current: CardRevision | null;
}

export interface CardDetail extends MemoryCard {
  history: CardRevision[];
}

export interface TimelineEntry {
  occurred_at: string;
  kind: string;
  headline: string;
  ref: string;
  topic_slug: string | null;
  evidence: Evidence | null;
}

export type PromiseStatus = "open" | "kept" | "broken" | "abandoned" | "unclear";

export interface Promise {
  statement: string;
  made_at: string;
  horizon: string | null;
  due_date: string | null;
  status: PromiseStatus;
  resolved_at: string | null;
  resolution_note: string | null;
  made_evidence: Evidence;
  resolved_evidence: Evidence | null;
}

export interface MemorySummary {
  company_id: string;
  ticker: string | null;
  document_count: number;
  counts: Record<string, number>;
  built_at: string | null;
}

// ------------------------------------------------------------ evidence viewer
// Mirrors EvidenceOut, ResolvedCitation and DocumentPagesOut in
// apps/api/schemas.py.

/** A rectangle on a page. Always null today — see BoundingBox in the API. */
export interface BoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface ResolvedEvidence {
  chunk_id: number;
  document_id: number;
  accession: string;
  form_type: string;
  filed_at: string;
  source_format: string;
  /** The page the cited paragraph is on, not the page its chunk starts on. */
  page: number | null;
  paragraph_id: string | null;
  paragraph_ids: string[];
  /** What the viewer highlights, in document order. The first is the scroll target. */
  anchors: string[];
  section_title: string | null;
  text: string;
  confidence: number | null;
  bounding_box: BoundingBox | null;
  citation: string;
}

export interface CitationRef {
  chunk_id: number;
  paragraph_id?: string | null;
  entity?: string | null;
}

/** One citation's outcome. Unresolved ones are kept, so indices still line up. */
export interface ResolvedCitation {
  index: number;
  resolved: boolean;
  evidence: ResolvedEvidence | null;
  error: string | null;
}

export interface DocumentBlock {
  anchor: string;
  kind: "paragraph" | "table";
  chunk_id: number;
  paragraph_id: string | null;
  section_title: string | null;
  text: string;
}

export interface DocumentPage {
  page: number | null;
  blocks: DocumentBlock[];
}

export interface DocumentView {
  document_id: number;
  accession: string;
  form_type: string;
  filed_at: string;
  source_format: string;
  ticker: string;
  page_count: number | null;
  pages: DocumentPage[];
}

/** Anything that makes a claim and cites its evidence. `[n]` markers in the
 *  text are rendered as links to citation n (1-based). */
export interface Answer {
  text: string;
  citations: ResolvedCitation[];
}

export interface EntityMention {
  observed_at: string;
  quote: string;
  accession: string;
  form_type: string;
  section_title: string | null;
  provenance: {
    chunk_id: number | null;
    chunk_hash: string | null;
    document_id: number;
    page: number | null;
    paragraph_id: string | null;
    confidence: number | null;
  };
}

export interface EntityDetail {
  id: number;
  entity_type: string;
  slug: string;
  name: string;
  description: string | null;
  mention_count: number;
  first_seen: string | null;
  latest_seen: string | null;
  mentions: EntityMention[];
}
