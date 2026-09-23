"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Answer, DocumentView, ResolvedCitation } from "@/lib/types";

/**
 * Split-screen evidence viewer: the claim and its citations on the left, the
 * filing on the right. Clicking a citation switches to its document, scrolls
 * its paragraph into view and keeps it highlighted.
 *
 * The highlight lives in React state and nowhere else. Scrolling never writes
 * to it — the page indicator listens to scroll on its own, and the document is
 * memoised so a scroll frame does not re-render a thousand paragraphs. That
 * separation is what keeps the highlight in place while the reader scrolls
 * away to check context and back again.
 */
export function EvidenceViewer({
  kicker,
  title,
  answer,
  documents,
}: {
  kicker?: string;
  title: string;
  answer: Answer;
  documents: Record<number, DocumentView>;
}) {
  const citations = answer.citations;
  const firstDoc =
    citations.find((c) => c.evidence)?.evidence?.document_id ??
    Number(Object.keys(documents)[0]);

  const [active, setActive] = useState<number | null>(null);
  const [docId, setDocId] = useState<number | null>(
    Number.isFinite(firstDoc) ? firstDoc : null,
  );
  // Bumped on every selection so re-clicking the same citation replays the
  // arrival flash and re-scrolls, even though `active` did not change.
  const [pulse, setPulse] = useState(0);
  const paneRef = useRef<HTMLDivElement>(null);

  const doc = docId != null ? documents[docId] : undefined;
  const activeEvidence = active != null ? citations[active]?.evidence ?? null : null;

  const highlighted = useMemo(
    () =>
      new Set(
        activeEvidence && activeEvidence.document_id === docId
          ? activeEvidence.anchors
          : [],
      ),
    [activeEvidence, docId],
  );

  // Every paragraph this answer cites in the shown document, with the
  // citation numbers pointing at it — so cited text is visible while reading,
  // not only when its chip is clicked.
  const markers = useMemo(() => {
    const out = new Map<string, number[]>();
    citations.forEach((c, i) => {
      if (!c.evidence || c.evidence.document_id !== docId) return;
      for (const anchor of c.evidence.anchors) {
        out.set(anchor, [...(out.get(anchor) ?? []), i + 1]);
      }
    });
    return out;
  }, [citations, docId]);

  const documentsCited = useMemo(
    () => new Set(citations.flatMap((c) => (c.evidence ? [c.evidence.document_id] : []))),
    [citations],
  );

  const select = useCallback(
    (index: number) => {
      const evidence = citations[index]?.evidence;
      if (!evidence) return;
      setActive(index);
      setDocId(evidence.document_id);
      setPulse((p) => p + 1);
    },
    [citations],
  );

  // Runs after the citation's document has committed, so its anchors exist.
  useEffect(() => {
    if (!activeEvidence || activeEvidence.document_id !== docId) return;
    const pane = paneRef.current;
    const target = activeEvidence.anchors[0];
    if (!pane || !target) return;
    const el = pane.querySelector<HTMLElement>(anchorSelector(target));
    if (!el) return;
    const offset =
      el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
    pane.scrollTo({
      // leave the page label and a line of context above the paragraph
      top: Math.max(0, offset - 96),
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [activeEvidence, docId, pulse]);

  return (
    <div className="ev-shell">
      <section className="ev-answer" aria-label="Claim and citations">
        {kicker && <p className="ev-kicker">{kicker}</p>}
        <h1 className="ev-title">{title}</h1>

        <p className="ev-text">
          {splitMarkers(answer.text).map((part, i) =>
            typeof part === "number" && citations[part - 1] ? (
              <button
                key={i}
                type="button"
                className="ev-marker"
                disabled={!citations[part - 1].evidence}
                aria-label={`Show citation ${part}`}
                onClick={() => select(part - 1)}
              >
                [{part}]
              </button>
            ) : (
              <span key={i}>{typeof part === "number" ? `[${part}]` : part}</span>
            ),
          )}
        </p>

        <ul className="ev-chips" aria-label="Citations">
          {citations.map((c, i) => (
            <li key={i}>
              <CitationChip
                n={i + 1}
                citation={c}
                active={i === active}
                showDocument={documentsCited.size > 1}
                onSelect={() => select(i)}
              />
            </li>
          ))}
        </ul>

        {activeEvidence && (
          <figure className="ev-quote" aria-live="polite">
            <blockquote>{activeEvidence.text}</blockquote>
            <figcaption>
              <span>{activeEvidence.citation}</span>
              <span
                className="ev-conf"
                title="Extraction confidence — the model's self-report, not a calibrated probability"
              >
                {activeEvidence.confidence == null
                  ? "no confidence recorded"
                  : `${Math.round(activeEvidence.confidence * 100)}% confidence`}
              </span>
            </figcaption>
          </figure>
        )}
      </section>

      <section className="ev-doc" aria-label="Source document">
        {doc ? (
          <>
            <header className="ev-doc-head">
              {documentsCited.size > 1 ? (
                <div className="ev-tabs" role="tablist" aria-label="Cited documents">
                  {[...documentsCited].map((id) => (
                    <button
                      key={id}
                      type="button"
                      role="tab"
                      aria-selected={id === docId}
                      className="ev-tab"
                      onClick={() => setDocId(id)}
                    >
                      {documents[id]?.form_type ?? "?"} · {documents[id]?.filed_at ?? id}
                    </button>
                  ))}
                </div>
              ) : (
                <span className="ev-doc-name">
                  {doc.ticker} {doc.form_type} · {doc.filed_at}
                </span>
              )}
              <PageIndicator
                key={doc.document_id}
                paneRef={paneRef}
                total={doc.page_count}
                first={doc.pages[0]?.page ?? null}
              />
            </header>
            <div className="ev-pages" ref={paneRef} data-testid="document-pane">
              <DocumentPages
                doc={doc}
                highlighted={highlighted}
                markers={markers}
                pulse={pulse}
              />
            </div>
          </>
        ) : (
          <p className="ev-empty">None of these citations could be resolved to a document.</p>
        )}
      </section>
    </div>
  );
}

function CitationChip({
  n,
  citation,
  active,
  showDocument,
  onSelect,
}: {
  n: number;
  citation: ResolvedCitation;
  active: boolean;
  showDocument: boolean;
  onSelect: () => void;
}) {
  const ev = citation.evidence;
  if (!ev) {
    // Kept in place rather than dropped, so [3] in the text still means chip 3.
    return (
      <button
        type="button"
        className="ev-chip is-unresolved"
        disabled
        title={citation.error ?? "could not be resolved"}
      >
        <span className="ev-chip-n">{n}</span> unavailable
      </button>
    );
  }
  return (
    <button
      type="button"
      className={`ev-chip${active ? " is-active" : ""}`}
      aria-pressed={active}
      title={ev.citation}
      onClick={onSelect}
    >
      <span className="ev-chip-n">{n}</span>
      {ev.form_type}
      {showDocument && ` ${ev.filed_at.slice(0, 7)}`} · p.&nbsp;{ev.page ?? "—"}
    </button>
  );
}

/** Memoised: only a new document, a new highlight or a replayed pulse
 *  re-renders the pages — never a scroll. */
const DocumentPages = memo(function DocumentPages({
  doc,
  highlighted,
  markers,
  pulse,
}: {
  doc: DocumentView;
  highlighted: Set<string>;
  markers: Map<string, number[]>;
  pulse: number;
}) {
  return (
    <>
      {doc.pages.map((page) => (
        <article
          key={page.page ?? "unpaged"}
          className="ev-page"
          data-page={page.page ?? ""}
          aria-label={page.page ? `Page ${page.page}` : "Unpaged text"}
        >
          <div className="ev-page-no">{page.page ? `Page ${page.page}` : "Unpaged"}</div>
          {page.blocks.map((block) => {
            const on = highlighted.has(block.anchor);
            const cited = markers.get(block.anchor);
            return (
              <div
                key={block.anchor}
                data-anchor={block.anchor}
                className={`ev-block${on ? " is-cited" : ""}${cited ? " is-referenced" : ""}`}
                aria-current={on ? "location" : undefined}
              >
                {cited && (
                  <span className="ev-gutter" aria-label={`cited as ${cited.join(", ")}`}>
                    {cited.join(" ")}
                  </span>
                )}
                {on && <span key={pulse} className="ev-flash" aria-hidden />}
                {block.kind === "table" ? (
                  <pre className="ev-table">{block.text}</pre>
                ) : (
                  <p>{block.text}</p>
                )}
              </div>
            );
          })}
        </article>
      ))}
    </>
  );
});

/** Which page is at the top of the pane. Owns its own state and its own
 *  scroll listener, so scrolling updates this label and nothing else. */
function PageIndicator({
  paneRef,
  total,
  first,
}: {
  paneRef: React.RefObject<HTMLDivElement | null>;
  total: number | null;
  first: number | null;
}) {
  const [page, setPage] = useState<number | null>(first);

  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;
    // A flag rather than "is there a frame id", because the id is assigned
    // after requestAnimationFrame returns — if the callback ever ran before
    // that, the id would stick and every later scroll would be ignored.
    let frame = 0;
    let scheduled = false;
    const read = () => {
      scheduled = false;
      const top = pane.getBoundingClientRect().top + 120;
      let current: number | null = first;
      for (const el of pane.querySelectorAll<HTMLElement>("[data-page]")) {
        if (el.getBoundingClientRect().top > top) break;
        const n = Number(el.dataset.page);
        if (Number.isFinite(n) && n > 0) current = n;
      }
      setPage(current);
    };
    const onScroll = () => {
      if (scheduled) return;
      scheduled = true;
      frame = requestAnimationFrame(read);
    };
    pane.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      pane.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(frame);
    };
  }, [paneRef, first]);

  return (
    <span className="ev-pageind" data-testid="page-indicator">
      p. {page ?? "—"}
      {total ? ` / ${total}` : ""}
    </span>
  );
}

/** "Export controls restrict shipments [1][2]." → text and citation numbers. */
export function splitMarkers(text: string): Array<string | number> {
  const out: Array<string | number> = [];
  const re = /\[(\d+)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(Number(m[1]));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Split paragraph ids carry `#` (`3_10#2`), so anchors are matched as quoted
 *  attribute values, where only quotes and backslashes need escaping. */
function anchorSelector(anchor: string): string {
  return `[data-anchor="${anchor.replace(/["\\]/g, "\\$&")}"]`;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
