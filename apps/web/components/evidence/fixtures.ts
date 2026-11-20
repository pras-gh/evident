/** Test fixtures shaped like the API's responses for the NVIDIA corpus: two
 *  filings, rendered, with citations of one entity across both. */
import type {
  DocumentDetail,
  DocumentView,
  ResolvedCitation,
  ResolvedEvidence,
  TimelineEvent,
} from "@/lib/types";

const RED = "#f87171";

export const fy2025: DocumentDetail = {
  document_id: 2, ticker: "NVDA", company: "NVIDIA CORP", accession: "0001045810-25-000023",
  form_type: "10-K", fiscal_period: "FY2025", filed_at: "2025-02-26", source_format: "html",
  page_count: 87, rendered_at: "2026-09-24T02:28:22Z", pdf_url: "/v1/document/2/pdf?v=1",
  url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/0001045810-25-000023-index.htm",
  pages: [
    { page: 1, width: 612, height: 792, image_url: null, thumbnail_url: null, paragraph_count: 0 },
    { page: 26, width: 612, height: 792, image_url: "/v1/document/2/page/26/image?v=1",
      thumbnail_url: "/v1/document/2/page/26/image?v=1&size=thumb", paragraph_count: 4 },
    { page: 27, width: 612, height: 1224, image_url: "/v1/document/2/page/27/image?v=1",
      thumbnail_url: "/v1/document/2/page/27/image?v=1&size=thumb", paragraph_count: 13 },
  ],
};

export const fy2026: DocumentDetail = {
  ...fy2025, document_id: 1, accession: "0001045810-26-000021", fiscal_period: "FY2026",
  filed_at: "2026-02-25", page_count: 85, pdf_url: "/v1/document/1/pdf?v=1",
  pages: [
    { page: 19, width: 612, height: 792, image_url: "/v1/document/1/page/19/image?v=1",
      thumbnail_url: "/v1/document/1/page/19/image?v=1&size=thumb", paragraph_count: 2 },
  ],
};

/** The same filing, never rendered: the viewer must fall back to text. */
export const unrendered: DocumentDetail = {
  ...fy2025, document_id: 3, accession: "0001045810-24-000029", fiscal_period: "FY2024",
  rendered_at: null, pdf_url: null, pages: fy2025.pages.map((p) => ({ ...p, image_url: null, thumbnail_url: null })),
};

export const unrenderedText: DocumentView = {
  document_id: 3, accession: unrendered.accession, form_type: "10-K", filed_at: "2024-02-21",
  source_format: "html", ticker: "NVDA", page_count: 85,
  pages: [{ page: 26, blocks: [
    { anchor: "p-26_4", kind: "paragraph", chunk_id: 70, paragraph_id: "26_4",
      section_title: "Item 1A", text: "Export controls could disrupt our supply chain.",
      bounding_box: null },
  ] }],
};

export function evidence(over: Partial<ResolvedEvidence> & { paragraph_id: string }): ResolvedEvidence {
  const page = Number(over.paragraph_id.split("_")[0]);
  const box = { page, x0: 72, y0: 90, x1: 540, y1: 130, page_width: 612, page_height: 792 };
  const base: ResolvedEvidence = {
    chunk_id: 54, document_id: 2, ticker: "NVDA", company: "NVIDIA CORP",
    accession: fy2025.accession, form_type: "10-K", fiscal_period: "FY2025",
    filed_at: "2025-02-26", source_format: "html", page, paragraph_id: over.paragraph_id,
    paragraph_ids: [over.paragraph_id], anchors: [`p-${over.paragraph_id}`],
    section_title: "Item 1A. Risk Factors",
    text: "Over the past three years, we have been subject to a series of shifting and expanding export controls.",
    confidence: 0.97, bounding_box: box,
    highlights: [{ anchor: `p-${over.paragraph_id}`, paragraph_id: over.paragraph_id, page,
                   bounding_box: box, highlight_color: RED }],
    highlight_color: RED,
    entities: [
      { slug: "export_controls", name: "Export Controls", entity_type: "risk", confidence: 0.97, highlight_color: RED },
      { slug: "china", name: "China", entity_type: "geography", confidence: 0.9, highlight_color: "#22d3ee" },
    ],
    citation: `10-K · p. ${page} · Item 1A. Risk Factors`,
  };
  return { ...base, ...over };
}

export const citations: ResolvedCitation[] = [
  // FY2026 first, as the page orders them: newest filing first
  { index: 0, resolved: true, error: null, evidence: evidence({
      paragraph_id: "19_1", chunk_id: 5, document_id: 1, fiscal_period: "FY2026",
      filed_at: "2026-02-25", accession: fy2026.accession }) },
  { index: 1, resolved: true, error: null, evidence: evidence({ paragraph_id: "26_3", chunk_id: 53 }) },
  { index: 2, resolved: true, error: null, evidence: evidence({ paragraph_id: "27_1" }) },
  { index: 3, resolved: false, error: "no chunk 999", evidence: null },
];

export const claims: TimelineEvent[] = [
  {
    id: "fy26", date: "2026-02-25", date_label: "Feb 2026", kind: "expanded", category: "Risk",
    title: "Export Controls Expanded",
    summary: "Cited in 26 paragraphs, up from 20 in the FY2025 10-K.",
    topic: { slug: "export_controls", name: "Export Controls", entity_type: "risk" },
    filing: { document_id: 1, accession: fy2026.accession, form_type: "10-K", fiscal_period: "FY2026", filed_at: "2026-02-25" },
    compared_with: null, paragraphs: 26, previous_paragraphs: 20, evidence: null,
  },
  {
    id: "fy25", date: "2025-02-26", date_label: "Feb 2025", kind: "expanded", category: "Risk",
    title: "Export Controls Expanded",
    summary: "Cited in 20 paragraphs, up from 13 in the FY2024 10-K.",
    topic: { slug: "export_controls", name: "Export Controls", entity_type: "risk" },
    filing: { document_id: 2, accession: fy2025.accession, form_type: "10-K", fiscal_period: "FY2025", filed_at: "2025-02-26" },
    compared_with: null, paragraphs: 20, previous_paragraphs: 13, evidence: null,
  },
];
