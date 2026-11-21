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
/** Where a paragraph sits on its page: points from the page's top-left, y
 *  down, as pdf.js draws it. Scale by `renderedWidth / page_width` to overlay.
 *  PDF filings only — null for HTML, where the anchor is the exact target. */
export interface BoundingBox {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  page_width: number;
  page_height: number;
}

/** One thing a citation lights up, with its own page and box. */
export interface Highlight {
  anchor: string;
  paragraph_id: string | null;
  page: number | null;
  bounding_box: BoundingBox | null;
  /** `#rrggbb` — the cited entity's colour */
  highlight_color: string;
}

/** An entity extraction found in a cited paragraph. */
export interface EvidenceEntity {
  slug: string;
  name: string;
  entity_type: string;
  confidence: number | null;
  highlight_color: string;
}

export interface ResolvedEvidence {
  chunk_id: number;
  document_id: number;
  ticker: string | null;
  company: string | null;
  accession: string;
  form_type: string;
  fiscal_period: string | null;
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
  /** every anchor to highlight, each on its own page — a chunk-wide
   *  citation can cross a page break */
  highlights: Highlight[];
  highlight_color: string;
  /** entities extracted from the cited paragraph, most confident first */
  entities: EvidenceEntity[];
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
  bounding_box: BoundingBox | null;
}

/** GET /v1/company/{ticker}/documents */
export interface DocumentSummary {
  document_id: number;
  accession: string;
  form_type: string;
  fiscal_period: string | null;
  filed_at: string;
  published_at: string;
  source_format: string;
  page_count: number | null;
  pages_with_text: number;
  first_page: number | null;
  paragraph_count: number;
  has_bounding_boxes: boolean;
  url: string;
}

/** A page as rendered: images and size. Boxes on it are in the same points. */
export interface PageSummary {
  page: number;
  width: number;
  height: number;
  /** API paths (`/v1/…`); pass through `asset()` for the browser */
  image_url: string | null;
  thumbnail_url: string | null;
  paragraph_count: number;
}

/** GET /v1/document/{id} */
export interface DocumentDetail {
  document_id: number;
  ticker: string | null;
  company: string;
  accession: string;
  form_type: string;
  fiscal_period: string | null;
  filed_at: string;
  source_format: string;
  page_count: number | null;
  /** null until rendered; the viewer then shows the filing as text */
  rendered_at: string | null;
  pdf_url: string | null;
  url: string;
  pages: PageSummary[];
}

/** GET /v1/document/{id}/page/{page} */
export interface PageView {
  document_id: number;
  accession: string;
  form_type: string;
  filed_at: string;
  ticker: string | null;
  source_format: string;
  page: number;
  page_count: number | null;
  page_width: number | null;
  page_height: number | null;
  prev_page: number | null;
  next_page: number | null;
  blocks: DocumentBlock[];
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
  thresholds: { min_delta: number; min_ratio: number; carried_over?: number };
  /** events per category, ignoring the category filter */
  categories: Record<string, number>;
  total: number;
  /** new or dropped topics extraction implied but the text did not: a
   *  sentence quoted for each is in both filings. Not in `events`. */
  withheld?: number;
  events: TimelineEvent[];
}

// ------------------------------------------------------------------ search

/** One passage from POST /v1/search, with the citation it came from. */
export interface SearchHit {
  chunk_id: number;
  chunk_hash: string;
  paragraph_ids: string[];
  /** cosine similarity, 0–1 */
  score: number;
  text: string;
  accession: string;
  form_type: string;
  filed_at: string;
  page_number: number | null;
  section_title: string | null;
  citation: string;
}

/** A company a search names; `covered` when Evident holds its filings. */
export interface SearchCompany {
  name: string;
  ticker: string | null;
  covered: boolean;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  took_ms: number;
  /** null when nothing was searched (only uncovered companies named) */
  embedder: string | null;
  /** every company the query names, covered or not */
  companies: SearchCompany[];
  /** tickers searched (null = every company on record), and why */
  scope: { tickers: string[] | null; reason: "named" | "filter" | "all" } | null;
  /** what the query asks once the companies are taken out */
  topic: string | null;
  /** every company Evident holds filings for */
  on_record: SearchCompany[];
  /** "rules", or "rules+provider:model" when a model also read the query */
  understood_by: string;
}

/** A search either answers, or says why it could not: the API's 503 `error`
 *  codes, "too_short" for a query the API refuses as under two characters,
 *  and "unreachable" when the API itself did not answer. */
export type SearchOutcome =
  | { ok: true; result: SearchResponse }
  | { ok: false; error: "search_not_configured" | "search_not_indexed" | "search_unavailable"
        | "too_short" | "unreachable" | "failed"; message: string };
