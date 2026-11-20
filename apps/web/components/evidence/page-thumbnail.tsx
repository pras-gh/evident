"use client";

import { useEffect, useRef } from "react";
import { asset } from "@/lib/api";
import type { PageSummary } from "@/lib/types";

/** One page in the rail: its thumbnail, its number, and a dot for each
 *  citation on it, in the citation's colour. */
export function PageThumbnail({
  page,
  current,
  marks,
  onSelect,
}: {
  page: PageSummary;
  current: boolean;
  marks: string[];
  onSelect: (page: number) => void;
}) {
  return (
    <button
      type="button"
      className={`thumb${current ? " is-current" : ""}${marks.length ? " is-cited" : ""}`}
      aria-current={current ? "page" : undefined}
      aria-label={`Page ${page.page}${marks.length ? `, ${marks.length} citation${marks.length > 1 ? "s" : ""}` : ""}`}
      onClick={() => onSelect(page.page)}
    >
      <span className="thumb-img" style={{ aspectRatio: `${page.width} / ${page.height}` }}>
        {page.thumbnail_url && (
          <img src={asset(page.thumbnail_url)} alt="" loading="lazy" decoding="async" />
        )}
      </span>
      <span className="thumb-foot">
        <span className="thumb-no">{page.page}</span>
        {marks.slice(0, 4).map((c, i) => (
          <i key={i} className="thumb-dot" style={{ background: c }} aria-hidden />
        ))}
      </span>
    </button>
  );
}

/** Every rendered page, top to bottom; the current one kept in view. */
export function PageThumbnailRail({
  pages,
  current,
  marks,
  onSelect,
}: {
  pages: PageSummary[];
  current: number | null;
  marks: Map<number, string[]>;
  onSelect: (page: number) => void;
}) {
  const ref = useRef<HTMLOListElement>(null);

  useEffect(() => {
    const el = ref.current?.querySelector<HTMLElement>('[aria-current="page"]');
    // jsdom has no scrollIntoView
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
  }, [current]);

  return (
    <nav className="thumbs" aria-label="Pages">
      <ol ref={ref}>
        {pages.map((p) => (
          <li key={p.page}>
            <PageThumbnail
              page={p}
              current={p.page === current}
              marks={marks.get(p.page) ?? []}
              onSelect={onSelect}
            />
          </li>
        ))}
      </ol>
    </nav>
  );
}
