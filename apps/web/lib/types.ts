// Mirrors apps/api/schemas.py. Evidence is required on anything that makes a
// claim, so a component cannot render an uncited assertion by accident.

/** Where a card fact comes from — CardEvidenceOut. */
export interface Evidence {
  document_id: number;
  accession: string | null;
  form_type: string | null;
  page_number: number | null;
  paragraph_id: string | null;
  quote: string;
  section_path: string[];
  /** with entity_slug, opens the paragraph in the evidence viewer */
  chunk_id: number | null;
  entity_slug: string | null;
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
  /** why the card has no history, when the stored data cannot give it one */
  unavailable: string | null;
}

export interface CardDetail extends MemoryCard {
  history: CardRevision[];
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

/** GET /v1/companies/{ticker} */
export interface MemorySummary {
  company_id: number;
  cik: string;
  ticker: string | null;
  name: string;
  document_count: number;
  earliest_filing: string | null;
  latest_filing: string | null;
  /** entities per type */
  counts: Record<string, number>;
  built_at: string | null;
}

/** GET /v1/companies */
export interface CompanyListItem {
  ticker: string | null;
  name: string;
  cik: string;
  document_count: number;
  latest_filing: string | null;
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

// ------------------------------------------------------------------ timeline
// GET /v1/company/{ticker}/timeline — what changed between filings.

export type TimelineKind =
  | "newly_disclosed"
  | "disclosed_again"
  | "expanded"
  | "narrowed"
  | "no_longer_disclosed"
  | "filed";

export interface TimelineFilingRef {
  document_id: number;
  accession: string;
  form_type: string;
  fiscal_period: string | null;
  filed_at: string;
}

export interface TimelineFiling extends TimelineFilingRef {
  page_count: number | null;
  /** baseline: the earliest of its form, so nothing in it is marked new */
  coverage: "baseline" | "compared" | "not compared";
  /** the filing's index page on EDGAR */
  url: string;
}

export interface TimelineEvidence {
  chunk_id: number | null;
  paragraph_id: string | null;
  document_id: number;
  page: number | null;
  quote: string | null;
  confidence: number | null;
  /** true when last year's filing has no close match for this paragraph */
  new_paragraph: boolean | null;
  citation: string;
}

export interface TimelineEvent {
  id: string;
  date: string;
  /** "Feb 2025" */
  date_label: string;
  kind: TimelineKind;
  /** "Risk", "Product", … or "Filing" */
  category: string;
  title: string;
  summary: string;
  topic: { slug: string; name: string; entity_type: string } | null;
  filing: TimelineFilingRef;
  compared_with: TimelineFilingRef | null;
  paragraphs: number | null;
  previous_paragraphs: number | null;
  /** null only for `filed` events; every change points at a paragraph */
  evidence: TimelineEvidence | null;
}

export interface CompanyTimeline {
  ticker: string;
  company: string;
  filings: TimelineFiling[];
  thresholds: { min_delta: number; min_ratio: number };
  /** events per category, ignoring the category filter */
  categories: Record<string, number>;
  total: number;
  events: TimelineEvent[];
}
