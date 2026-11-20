import { asset } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";

export const ZOOMS = [0.75, 1, 1.25, 1.5] as const;

/** Across the top of the filing: which filing and page, stepping through
 *  citations, zoom, and the filing itself as a PDF or on EDGAR. */
export function EvidenceToolbar({
  documents,
  documentId,
  onDocument,
  page,
  position,
  count,
  onPrev,
  onNext,
  zoom,
  onZoom,
}: {
  documents: DocumentDetail[];
  documentId: number;
  onDocument: (id: number) => void;
  page: number | null;
  position: number;
  count: number;
  onPrev: () => void;
  onNext: () => void;
  zoom: number;
  onZoom: (z: number) => void;
}) {
  const doc = documents.find((d) => d.document_id === documentId);
  const z = ZOOMS.indexOf(zoom as (typeof ZOOMS)[number]);
  return (
    <header className="tb" role="toolbar" aria-label="Filing">
      <div className="tb-docs" role="tablist" aria-label="Cited filings">
        {documents.map((d) => (
          <button key={d.document_id} type="button" role="tab" className="tb-doc"
            aria-selected={d.document_id === documentId}
            onClick={() => onDocument(d.document_id)}>
            {d.fiscal_period ?? d.filed_at.slice(0, 4)} {d.form_type}
          </button>
        ))}
      </div>

      <span className="tb-page" data-testid="page-indicator">
        p. {page ?? "—"}
        {doc?.page_count ? ` / ${doc.page_count}` : ""}
      </span>

      <div className="tb-group" aria-label="Citations">
        <button type="button" className="tb-btn" onClick={onPrev} disabled={position <= 1}
          aria-label="Previous citation">‹</button>
        <span className="tb-count">{position} / {count}</span>
        <button type="button" className="tb-btn" onClick={onNext} disabled={position >= count}
          aria-label="Next citation">›</button>
      </div>

      <div className="tb-group" aria-label="Zoom">
        <button type="button" className="tb-btn" aria-label="Zoom out"
          disabled={z <= 0} onClick={() => onZoom(ZOOMS[Math.max(0, z - 1)])}>−</button>
        <span className="tb-count">{Math.round(zoom * 100)}%</span>
        <button type="button" className="tb-btn" aria-label="Zoom in"
          disabled={z >= ZOOMS.length - 1}
          onClick={() => onZoom(ZOOMS[Math.min(ZOOMS.length - 1, z + 1)])}>+</button>
      </div>

      <div className="tb-links">
        {doc?.pdf_url && (
          <a className="tb-link" href={asset(doc.pdf_url)} target="_blank" rel="noreferrer">PDF</a>
        )}
        {doc && (
          <a className="tb-link" href={doc.url} target="_blank" rel="noreferrer">EDGAR ↗</a>
        )}
      </div>
    </header>
  );
}
