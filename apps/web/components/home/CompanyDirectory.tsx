"use client";

import { useDeferredValue, useMemo, useState } from "react";
import type { CompanyListItem } from "@/lib/types";

const OTHER = "Other companies";

/** Lower case, accents and punctuation gone: "Moody's" finds "moodys". */
function fold(s: string): string {
  return s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/['’]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

/** An id for a sector heading; ids cannot hold spaces. */
const slug = (sector: string) => `sector-${fold(sector).replace(/ /g, "-")}`;

/** Filings downloaded and still being embedded. A company whose filings
 * are only indexed (downloaded on demand) is not "indexing": it is waiting
 * for someone to ask. */
const indexing = (c: CompanyListItem) => c.document_count > 0 && !c.searchable;

function matches(c: CompanyListItem, q: string): boolean {
  if (!q) return true;
  const ticker = (c.ticker ?? "").toLowerCase();
  if (ticker.startsWith(q.replace(/^\$/, ""))) return true;
  const f = fold(q);
  return !!f && (fold(c.display_name).includes(f) || fold(c.name).includes(f));
}

/**
 * Every company on record, by GICS sector, with a filter. Five hundred
 * companies is a directory, not a list of chips: the filter is how most
 * people use it, and the sectors are for browsing.
 */
export function CompanyDirectory({ companies }: { companies: CompanyListItem[] }) {
  const [query, setQuery] = useState("");
  const q = useDeferredValue(query.trim().toLowerCase());

  const groups = useMemo(() => {
    const by = new Map<string, CompanyListItem[]>();
    for (const c of companies) {
      if (!c.ticker || !matches(c, q)) continue;
      const key = c.sector ?? OTHER;
      by.set(key, [...(by.get(key) ?? []), c]);
    }
    return [...by.entries()]
      .sort(([a], [b]) => (a === OTHER ? 1 : b === OTHER ? -1 : a.localeCompare(b)))
      .map(([sector, list]) => ({
        sector,
        list: list.sort((a, b) => a.display_name.localeCompare(b.display_name)),
      }));
  }, [companies, q]);
  const shown = groups.reduce((n, g) => n + g.list.length, 0);
  const total = companies.filter((c) => c.ticker).length;

  return (
    <div className="dir">
      <div className="dir-filter">
        <svg aria-hidden="true" viewBox="0 0 16 16" width="15" height="15">
          <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.5 10.5 14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Filter ${total.toLocaleString()} companies by name or ticker`}
          aria-label="Filter companies"
          autoComplete="off"
          spellCheck={false}
        />
        {q && (
          <span className="dir-filter-count" aria-live="polite">
            {shown.toLocaleString()} of {total.toLocaleString()}
          </span>
        )}
      </div>

      {shown === 0 ? (
        <p className="dir-empty">
          No company on record matches “{query.trim()}”.{" "}
          <a href={`/search?q=${encodeURIComponent(query.trim())}`}>
            Search for it — Parallx fetches its filings from the SEC
          </a>
          .
        </p>
      ) : (
        groups.map(({ sector, list }) => (
          <section key={sector} className="dir-group" aria-labelledby={slug(sector)}>
            <h2 id={slug(sector)} className="dir-sector">
              {sector} <span className="dir-n">{list.length}</span>
            </h2>
            <ul className="dir-grid">
              {list.map((c) => (
                <li key={c.cik}>
                  <a
                    className="dir-co"
                    href={`/memory/${c.ticker!.toLowerCase()}`}
                    title={indexing(c) ? `${c.name} — filings downloaded, being indexed for search` : c.name}
                  >
                    <span className="dir-tk">{c.ticker}</span>
                    <span className="dir-name">{c.display_name}</span>
                    {indexing(c) && <span className="dir-indexing" aria-label="indexing" />}
                  </a>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
