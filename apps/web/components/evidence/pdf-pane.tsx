"use client";

import { memo, useEffect } from "react";
import { asset } from "@/lib/client-api";
import type { BoundingBox, DocumentDetail, DocumentView } from "@/lib/types";
import { HighlightOverlay } from "./highlight-overlay";

/** A citation as the pane draws it: where, in what colour, and which number. */
export interface PaneHighlight {
  key: string;
  anchor: string;
  page: number | null;
  box: BoundingBox | null;
  color: string;
  n: number;
  active: boolean;
}

/** Where to take the reader: a page, and the box on it if there is one. */
export interface PaneTarget {
  page: number | null;
  box: BoundingBox | null;
  anchor: string;
}

/** Space left above the target paragraph: the page label and a line of context. */
export const SCROLL_OFFSET = 96;

/**
 * The filing, page by page, with citations drawn over their paragraphs.
 *
 * Rendered filings show their page images; each page element keeps the page's
 * aspect ratio, so its height is right before the image arrives and a scroll
 * target computed on first paint is where the paragraph will be. A filing not
 * rendered yet falls back to its text, highlighted by anchor.
 *
 * Scrolling never touches the highlight: the pages are memoised on the
 * citation state, and the current page is reported through a callback from
 * its own listener.
 */
export function PdfPane({
  doc,
  text,
  highlights,
  target,
  pulse,
  zoom,
  onPage,
  paneRef,
}: {
  doc: DocumentDetail;
  text?: DocumentView;
  highlights: PaneHighlight[];
  target: PaneTarget | null;
  pulse: number;
  zoom: number;
  onPage: (page: number | null) => void;
  paneRef: React.RefObject<HTMLDivElement | null>;
}) {
  const pages = doc.pages.filter((p) => p.image_url);
  const rendered = pages.length > 0;

  // Take the reader to the target once its page is laid out. Arriving by link
  // (no click yet, pulse 0) lands; a click animates.
  useEffect(() => {
    const pane = paneRef.current;
    if (!pane || !target) return;
    const el = pane.querySelector<HTMLElement>(
      rendered
        ? `[data-page="${target.page ?? ""}"]`
        : `[data-anchor="${target.anchor.replace(/["\\]/g, "\\$&")}"]`,
    );
    if (!el) return;
    const page = doc.pages.find((p) => p.page === target.page);
    const within =
      rendered && target.box && page ? (target.box.y0 / page.height) * el.clientHeight : 0;
    const top =
      el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop + within;
    pane.scrollTo({
      top: Math.max(0, top - SCROLL_OFFSET),
      behavior: prefersReducedMotion() || pulse === 0 ? "auto" : "smooth",
    });
  }, [target, pulse, zoom, doc, rendered, paneRef]);

  // Which page is at the top of the pane — rAF-throttled, and a flag rather
  // than a frame id, which is assigned only after requestAnimationFrame returns.
  useEffect(() => {
    const pane = paneRef.current;
    if (!pane) return;
    let scheduled = false;
    let frame = 0;
    const read = () => {
      scheduled = false;
      const line = pane.getBoundingClientRect().top + SCROLL_OFFSET + 24;
      let current: number | null = null;
      for (const el of pane.querySelectorAll<HTMLElement>("[data-page]")) {
        if (el.getBoundingClientRect().top > line) break;
        const n = Number(el.dataset.page);
        if (Number.isFinite(n) && n > 0) current = n;
      }
      if (current == null) {
        const first = Number(pane.querySelector<HTMLElement>("[data-page]")?.dataset.page);
        current = Number.isFinite(first) && first > 0 ? first : null;
      }
      onPage(current);
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
  }, [paneRef, onPage, doc]);

  return (
    <div className="pp" ref={paneRef} data-testid="document-pane">
      {rendered ? (
        <Pages doc={doc} highlights={highlights} pulse={pulse} zoom={zoom} />
      ) : text ? (
        <TextPages doc={text} highlights={highlights} pulse={pulse} />
      ) : (
        <p className="pp-empty">This filing has not been rendered yet.</p>
      )}
    </div>
  );
}

const Pages = memo(function Pages({
  doc,
  highlights,
  pulse,
  zoom,
}: {
  doc: DocumentDetail;
  highlights: PaneHighlight[];
  pulse: number;
  zoom: number;
}) {
  const onPage = new Map<number, PaneHighlight[]>();
  for (const h of highlights) {
    if (h.page != null && h.box) onPage.set(h.page, [...(onPage.get(h.page) ?? []), h]);
  }
  return (
    <div className="pp-pages" style={{ ["--zoom" as string]: zoom }}>
      {doc.pages
        .filter((p) => p.image_url)
        .map((p) => (
          <article
            key={p.page}
            className="pp-page"
            data-page={p.page}
            style={{ aspectRatio: `${p.width} / ${p.height}` }}
            aria-label={`Page ${p.page}`}
          >
            <img src={asset(p.image_url!)} alt={`Page ${p.page} of the filing`}
              loading="lazy" decoding="async" draggable={false} />
            {/* inactive first, so the active highlight paints on top */}
            {(onPage.get(p.page) ?? [])
              .slice()
              .sort((a, b) => Number(a.active) - Number(b.active))
              .map((h) => (
                <HighlightOverlay key={h.key} box={h.box!} pageWidth={p.width}
                  pageHeight={p.height} color={h.color} active={h.active}
                  n={h.n} pulse={h.active ? pulse : undefined} anchor={h.anchor} />
              ))}
            <span className="pp-page-no" aria-hidden>
              {p.page}
            </span>
          </article>
        ))}
    </div>
  );
});

/** The filing as text, for filings not rendered yet: the same citations,
 *  highlighted by paragraph anchor instead of by box. */
const TextPages = memo(function TextPages({
  doc,
  highlights,
  pulse,
}: {
  doc: DocumentView;
  highlights: PaneHighlight[];
  pulse: number;
}) {
  // one highlight per paragraph; when two citations share it, the active wins
  const byAnchor = new Map<string, PaneHighlight>();
  for (const h of highlights) {
    if (!byAnchor.get(h.anchor)?.active) byAnchor.set(h.anchor, h);
  }
  return (
    <div className="pp-text">
      {doc.pages.map((page) => (
        <article key={page.page ?? "unpaged"} className="ev-page" data-page={page.page ?? ""}
          aria-label={page.page ? `Page ${page.page}` : "Unpaged text"}>
          <div className="ev-page-no">{page.page ? `Page ${page.page}` : "Unpaged"}</div>
          {page.blocks.map((block) => {
            const h = byAnchor.get(block.anchor);
            return (
              <div key={block.anchor} data-anchor={block.anchor}
                className={`ev-block${h?.active ? " is-cited" : ""}${h ? " is-referenced" : ""}`}
                aria-current={h?.active ? "location" : undefined}
                style={h ? { ["--hl" as string]: h.color } : undefined}>
                {h?.active && <span key={pulse} className="ev-flash" aria-hidden />}
                {block.kind === "table" ? <pre className="ev-table">{block.text}</pre>
                  : <p>{block.text}</p>}
              </div>
            );
          })}
        </article>
      ))}
    </div>
  );
});

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
