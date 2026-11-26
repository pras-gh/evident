"use client";

import { ParallxMark } from "@/components/brand/ParallxMark";
import { ParallxWordmark } from "@/components/brand/ParallxWordmark";
import { useState } from "react";
import { MemoryCardTile } from "@/components/MemoryCardTile";
import { RevisionDrawer } from "@/components/RevisionDrawer";
import { TimelineRail } from "@/components/TimelineRail";
import { FilingsRail } from "@/components/company/FilingsRail";
import { FinancialsPanel } from "@/components/company/FinancialsPanel";
import { getCard } from "@/lib/api";
import type { CardDetail, Filing, Financials, MemoryCard, MemorySummary,
              TimelineEvent } from "@/lib/types";

export function MemoryDashboard({
  company,
  cards,
  timeline,
  financials = null,
  filings = [],
}: {
  company: MemorySummary;
  cards: MemoryCard[];
  timeline: TimelineEvent[];
  financials?: Financials | null;
  filings?: Filing[];
}) {
  const [open, setOpen] = useState<CardDetail | null>(null);

  // History is fetched on open rather than shipped with every tile — nine full
  // trails is a lot of payload for something most readers open one of.
  async function openCard(kind: string) {
    setOpen(await getCard(company.ticker ?? "", kind));
  }

  const t = (company.ticker ?? "").toLowerCase();
  // filings' text is downloaded on demand: until someone asks, there is an
  // index and statements, and nothing for memory to be built from
  const loaded = company.document_count > 0;

  return (
    <div className="shell">
      {/* .shell is a sidebar-and-content grid; without the sidebar the whole
          dashboard would render inside its 214px column */}
      <aside className="side" aria-label="Navigation">
        <a className="brand" href="/" aria-label="Parallx">
          <ParallxMark className="brand-mark" size={18} />
          <ParallxWordmark height={10} className="brand-word" />
        </a>
        <nav className="nav">
          <a className="nav-item is-on" href={`/memory/${t}`} aria-current="page">
            <span className="nav-dot" aria-hidden />
            Memory
          </a>
          <a className="nav-item" href={`/timeline/${t}`}>
            <span className="nav-dot" aria-hidden />
            Timeline
          </a>
          <a className="nav-item" href={`/search?ticker=${encodeURIComponent(t)}`}>
            <span className="nav-dot" aria-hidden />
            Search
          </a>
        </nav>
      </aside>

      <main className="main">
        <header className="head">
          <span className="tickmark">{company.ticker}</span>
          <div className="head-id">
            <h1>{company.ticker}</h1>
            <p className="head-sub">{company.name} · Company Memory</p>
            <p className="head-meta">
              {loaded ? (
                <>
                  Memory built from <b>{company.document_count.toLocaleString()} documents</b>
                  {company.earliest_filing && company.latest_filing && (
                    <>
                      , filed {company.earliest_filing} to {company.latest_filing}
                    </>
                  )}
                </>
              ) : (
                <>Financial statements and filings from SEC EDGAR</>
              )}
            </p>
          </div>
        </header>

        <div className="stage">
          <div className="panels">
            <FinancialsPanel ticker={company.ticker ?? ""} data={financials} />

            {loaded ? (
              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h2>Memory cards</h2>
                    <p className="panel-sub">
                      Every filing updates the cards it touches. Each card keeps its history.
                    </p>
                  </div>
                </div>
                <div className="grid">
                  {cards.map((c) => (
                    <MemoryCardTile key={c.kind} card={c} onOpen={openCard} />
                  ))}
                </div>
              </section>
            ) : (
              <section className="panel ondemand" aria-labelledby="od-h">
                <h2 id="od-h">Full filings load when you ask</h2>
                <p>
                  Parallx keeps every S&amp;P 500 company&rsquo;s filing list and statements
                  current, and downloads a filing&rsquo;s text the first time someone asks about
                  the company. Search it, and its latest annual report is fetched, read and made
                  searchable — every answer cited to the paragraph.
                </p>
                <a className="btn btn-ghost" href={`/search?q=${encodeURIComponent(company.name)}`}>
                  Search {company.ticker}&rsquo;s filings
                </a>
              </section>
            )}
          </div>

          <aside className="rails">
            {loaded && <TimelineRail ticker={company.ticker ?? ""} events={timeline} />}
            <FilingsRail filings={filings} />
          </aside>
        </div>
      </main>

      <RevisionDrawer card={open} onClose={() => setOpen(null)} />
    </div>
  );
}
