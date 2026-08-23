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
