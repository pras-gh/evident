"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  DocumentDetail,
  DocumentView,
  EvidenceEntity,
  ResolvedCitation,
  TimelineEvent,
} from "@/lib/types";
import { evidenceUrl } from "@/lib/cite";
import { CitationChip } from "./citation-chip";
import { EvidenceToolbar } from "./evidence-toolbar";
import { PageThumbnailRail } from "./page-thumbnail";
import { type PaneHighlight, type PaneTarget, PdfPane } from "./pdf-pane";

const SPLIT_KEY = "evident.evidence.split";
export const SPLIT = { min: 26, max: 60, initial: 36 } as const;

/** "Item 1A. Risk Factors" → "Item 1A" */
function shortSection(title: string | null): string | null {
  const m = title?.match(/^(item\s+\w+|part\s+[ivx]+)/i);
  return m ? m[1] : title;
}

/**
 * The evidence viewer: the claim on the left, the filing on the right.
 *
 * Selecting a citation — a chip, the arrows, a deep link — switches to its
 * filing, scrolls to its page and paragraph, and highlights it in the cited
 * entity's colour. The URL follows (`?cite=`), so what is on screen can be
 * linked to and reloaded.
 */
export function EvidenceViewer({
  entity,
  claims,
  citations,
  initialActive,
  documents,
  texts,
}: {
  entity: EvidenceEntity | null;
  claims: TimelineEvent[];
  citations: ResolvedCitation[];
  initialActive: number;
  documents: Record<number, DocumentDetail>;
  texts: Record<number, DocumentView>;
}) {
  const opening = citations[initialActive]?.evidence ? initialActive : 0;
  const [active, setActive] = useState(opening);
  // Bumped on every selection, so choosing the same citation again replays
  // the arrival and re-scrolls although `active` did not change.
  const [pulse, setPulse] = useState(0);
  const [split, setSplit] = useState<number>(SPLIT.initial);
  const [zoom, setZoom] = useState(1);
  const [page, setPage] = useState<number | null>(citations[opening]?.evidence?.page ?? null);
  const shellRef = useRef<HTMLDivElement>(null);
  const paneRef = useRef<HTMLDivElement>(null);

  const ev = citations[active]?.evidence ?? null;
  const [docId, setDocId] = useState<number | null>(ev?.document_id ?? null);
  const doc = docId != null ? documents[docId] : undefined;

  // The split is a per-reader preference; storage can be unavailable.
  useEffect(() => {
    try {
      const saved = Number(window.localStorage.getItem(SPLIT_KEY));
      if (saved >= SPLIT.min && saved <= SPLIT.max) setSplit(saved);
    } catch {}
  }, []);
  const saveSplit = useCallback((v: number) => {
    const clamped = Math.min(SPLIT.max, Math.max(SPLIT.min, v));
    setSplit(clamped);
    try {
      window.localStorage.setItem(SPLIT_KEY, String(Math.round(clamped)));
    } catch {}
  }, []);

  const select = useCallback(
    (i: number) => {
      const e = citations[i]?.evidence;
      if (!e) return;
      setActive(i);
      setDocId(e.document_id);
      setPage(e.page);
      setPulse((p) => p + 1);
      try {
        window.history.replaceState(null, "",
          evidenceUrl(e.chunk_id, e.paragraph_id, entity?.slug));
      } catch {}
    },
    [citations, entity],
  );

  const resolvedIdx = useMemo(
    () => citations.flatMap((c, i) => (c.evidence ? [i] : [])),
    [citations],
  );
  const position = resolvedIdx.indexOf(active) + 1;
  const step = (d: number) => {
    const next = resolvedIdx[resolvedIdx.indexOf(active) + d];
    if (next != null) select(next);
  };

  // Every citation in the shown filing, drawn on its page; the active one on top.
  const highlights = useMemo<PaneHighlight[]>(
    () =>
      citations.flatMap((c, i) =>
        c.evidence && c.evidence.document_id === docId
          ? c.evidence.highlights.map((h) => ({
              key: `${i}:${h.anchor}`,
              anchor: h.anchor,
              page: h.page,
              box: h.bounding_box,
              color: h.highlight_color,
              n: i + 1,
              active: i === active,
            }))
          : [],
      ),
    [citations, docId, active],
  );

  const target = useMemo<PaneTarget | null>(() => {
    if (!ev || ev.document_id !== docId) return null;
    const h = ev.highlights[0];
    return h ? { page: h.page, box: h.bounding_box, anchor: h.anchor }
      : { page: ev.page, box: ev.bounding_box, anchor: ev.anchors[0] ?? "" };
  }, [ev, docId]);

  const marks = useMemo(() => {
    const out = new Map<number, string[]>();
    for (const h of highlights) {
      if (h.page != null) out.set(h.page, [...(out.get(h.page) ?? []), h.color]);
    }
    return out;
  }, [highlights]);

  const cited = useMemo(
    () =>
      [...new Set(citations.flatMap((c) => (c.evidence ? [c.evidence.document_id] : [])))]
        .map((id) => documents[id])
        .filter(Boolean),
    [citations, documents],
  );

  const goToPage = useCallback(
    (n: number) => {
      const pane = paneRef.current;
      const el = pane?.querySelector<HTMLElement>(`[data-page="${n}"]`);
      if (!pane || !el) return;
      pane.scrollTo({
        top: el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 16,
        behavior: "smooth",
      });
    },
    [],
  );

  const showDocument = (id: number) => {
    const first = citations.findIndex((c) => c.evidence?.document_id === id);
    if (first >= 0) select(first);
    else setDocId(id);
  };

  // The claim for the filing being shown: the timeline event for this entity
  // in that filing, when there is one.
  const claim =
    (ev && claims.find((e) => e.kind !== "filed" && e.filing.document_id === ev.document_id)) ||
    null;

  return (
    <div className="evv" ref={shellRef}
      style={{ gridTemplateColumns: `${split}% 10px minmax(0, 1fr)` }}>
      <aside className="evv-left" aria-label="Evidence">
        {ev && (
          <article className="evv-card" style={{ ["--hl" as string]: ev.highlight_color }}>
            <p className="evv-kicker">
              <span className="evv-dot" aria-hidden />
              {ev.ticker ?? ""} · {entity?.entity_type ?? "citation"} · Evidence
            </p>
            <h1 className="evv-title">{entity?.name ?? ev.citation}</h1>
            <p className="evv-claim" data-testid="claim">
              {claim ? (
                <>
                  <b>{claim.title}.</b> {claim.summary}
                </>
              ) : entity ? (
                <>
                  Cited in {resolvedIdx.length} paragraph{resolvedIdx.length === 1 ? "" : "s"}{" "}
                  across {cited.length} filing{cited.length === 1 ? "" : "s"}.
                </>
              ) : (
                <>One paragraph, cited on its own.</>
              )}
            </p>
            <blockquote className="evv-quote">{ev.text}</blockquote>
            <dl className="evv-meta">
              <div>
                <dt>Confidence</dt>
                <dd title="The extractor's self-reported score — not a calibrated probability">
                  {ev.confidence == null ? (
                    "not recorded"
                  ) : (
                    <>
                      <span className="evv-meter" aria-hidden>
                        <i style={{ width: `${Math.round(ev.confidence * 100)}%` }} />
                      </span>
                      {Math.round(ev.confidence * 100)}%
                    </>
                  )}
                </dd>
              </div>
              <div>
                <dt>Source</dt>
                <dd data-testid="source">
                  {[`${ev.fiscal_period ?? ev.filed_at.slice(0, 4)} ${ev.form_type}`,
                    shortSection(ev.section_title),
                    ev.page ? `Page ${ev.page}` : null].filter(Boolean).join(" • ")}
                </dd>
              </div>
            </dl>
            <button type="button" className="evv-jump" onClick={() => select(active)}>
              Jump to Evidence <span aria-hidden>→</span>
            </button>
          </article>
        )}

        <nav className="evv-chips" aria-label="Citations">
          <p className="evv-chips-head">
            {citations.length} citation{citations.length === 1 ? "" : "s"}
          </p>
          <ul>
            {citations.map((c, i) => (
              <li key={i}>
                <CitationChip n={i + 1} citation={c} active={i === active}
                  showFiling={cited.length > 1} onSelect={() => select(i)} />
              </li>
            ))}
          </ul>
        </nav>

        {ev && ev.entities.some((e) => e.slug !== entity?.slug) && (
          <p className="evv-also">
            Also in this paragraph:{" "}
            {ev.entities
              .filter((e) => e.slug !== entity?.slug)
              .map((e, i) => (
                <span key={e.slug}>
                  {i > 0 && ", "}
                  <a href={evidenceUrl(ev.chunk_id, ev.paragraph_id, e.slug)}
                    style={{ ["--hl" as string]: e.highlight_color }}>
                    {e.name}
                  </a>
                </span>
              ))}
          </p>
        )}
      </aside>

      <div
        className="evv-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panels"
        aria-valuemin={SPLIT.min}
        aria-valuemax={SPLIT.max}
        aria-valuenow={Math.round(split)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowLeft") saveSplit(split - 2);
          else if (e.key === "ArrowRight") saveSplit(split + 2);
          else if (e.key === "Home") saveSplit(SPLIT.min);
          else if (e.key === "End") saveSplit(SPLIT.max);
          else return;
          e.preventDefault();
        }}
        onPointerDown={(e) => {
          const shell = shellRef.current;
          if (!shell) return;
          e.currentTarget.setPointerCapture?.(e.pointerId);
          const rect = shell.getBoundingClientRect();
          const move = (m: PointerEvent) => saveSplit(((m.clientX - rect.left) / rect.width) * 100);
          const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }}
      />

      <section className="evv-right" aria-label="Filing">
        {doc ? (
          <>
            <EvidenceToolbar documents={cited} documentId={doc.document_id}
              onDocument={showDocument} page={page} position={position}
              count={resolvedIdx.length} onPrev={() => step(-1)} onNext={() => step(1)}
              zoom={zoom} onZoom={setZoom} />
            <div className="evv-body">
              {doc.pages.some((p) => p.thumbnail_url) && (
                <PageThumbnailRail pages={doc.pages.filter((p) => p.image_url)}
                  current={page} marks={marks} onSelect={goToPage} />
              )}
              <PdfPane doc={doc} text={texts[doc.document_id]} highlights={highlights}
                target={target} pulse={pulse} zoom={zoom} onPage={setPage}
                paneRef={paneRef} />
            </div>
          </>
        ) : (
          <p className="pp-empty">None of these citations could be resolved to a filing.</p>
        )}
      </section>
    </div>
  );
}
