"use client";

import { useState } from "react";
import { MemoryCardTile } from "@/components/MemoryCardTile";
import { RevisionDrawer } from "@/components/RevisionDrawer";
import { TimelineRail } from "@/components/TimelineRail";
import { getCard } from "@/lib/api";
import type { CardDetail, MemoryCard, MemorySummary, TimelineEvent } from "@/lib/types";

export function MemoryDashboard({
  company,
  cards,
  timeline,
}: {
  company: MemorySummary;
  cards: MemoryCard[];
  timeline: TimelineEvent[];
}) {
  const [open, setOpen] = useState<CardDetail | null>(null);

  // History is fetched on open rather than shipped with every tile — nine full
  // trails is a lot of payload for something most readers open one of.
  async function openCard(kind: string) {
    setOpen(await getCard(company.ticker ?? "", kind));
  }

  const t = (company.ticker ?? "").toLowerCase();

  return (
    <div className="shell">
      {/* .shell is a sidebar-and-content grid; without the sidebar the whole
          dashboard would render inside its 214px column */}
      <aside className="side" aria-label="Navigation">
        <a className="brand" href="/">
          <span className="brand-mark" aria-hidden />
          <span>Evident</span>
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
        </nav>
      </aside>

      <main className="main">
        <header className="head">
          <span className="tickmark">{company.ticker}</span>
          <div className="head-id">
            <h1>{company.ticker}</h1>
            <p className="head-sub">{company.name} · Company Memory</p>
            <p className="head-meta">
              Memory built from <b>{company.document_count.toLocaleString()} documents</b>
              {company.earliest_filing && company.latest_filing && (
                <>
                  , filed {company.earliest_filing} to {company.latest_filing}
                </>
              )}
            </p>
          </div>
        </header>

        <div className="stage">
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

          <aside className="rails">
            <TimelineRail ticker={company.ticker ?? ""} events={timeline} />
          </aside>
        </div>
      </main>

      <RevisionDrawer
        card={open}
        ticker={company.ticker ?? ""}
        onClose={() => setOpen(null)}
      />
    </div>
  );
}
