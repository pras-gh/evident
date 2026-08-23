"use client";

import { useState } from "react";
import { MemoryCardTile } from "@/components/MemoryCardTile";
import { RevisionDrawer } from "@/components/RevisionDrawer";
import { TimelineRail } from "@/components/TimelineRail";
import { getCard } from "@/lib/api";
import type { CardDetail, MemoryCard, MemorySummary, TimelineEntry } from "@/lib/types";

export function MemoryDashboard({
  company,
  cards,
  timeline,
}: {
  company: MemorySummary;
  cards: MemoryCard[];
  timeline: TimelineEntry[];
}) {
  const [open, setOpen] = useState<CardDetail | null>(null);

  // History is fetched on open rather than shipped with every tile — nine full
  // trails is a lot of payload for something most readers open one of.
  async function openCard(kind: string) {
    setOpen(await getCard(company.ticker ?? "", kind));
  }

  return (
    <div className="shell">
      <main className="main">
        <header className="head">
          <span className="tickmark">{company.ticker}</span>
          <div className="head-id">
            <h1>{company.ticker}</h1>
            <p className="head-sub">Company Memory</p>
            <p className="head-meta">
              Memory built from <b>{company.document_count.toLocaleString()} documents</b>
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
            <TimelineRail entries={timeline} />
          </aside>
        </div>
      </main>

      <RevisionDrawer card={open} onClose={() => setOpen(null)} />
    </div>
  );
}
