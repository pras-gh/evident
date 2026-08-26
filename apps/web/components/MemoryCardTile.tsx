"use client";

import type { MemoryCard } from "@/lib/types";
import { RevisionRail } from "./RevisionRail";

const TONE: Record<string, string> = {
  revenue: "emerald", ai: "indigo", products: "orange", guidance: "teal",
  risks: "red", capital: "cyan", promises: "amber", headcount: "blue", rd: "violet",
};

export function MemoryCardTile({
  card,
  onOpen,
}: {
  card: MemoryCard;
  onOpen: (kind: string) => void;
}) {
  const current = card.current;
  const headline = current?.facts[0];

  return (
    <button
      className={`card t-${TONE[card.kind] ?? "emerald"}`}
      onClick={() => onOpen(card.kind)}
      aria-label={`${card.title} — ${card.revision_count} revisions`}
    >
      <div className="card-top">
        <span className="badge" aria-hidden />
        <span className="card-title">{card.title}</span>
      </div>

      <div className="card-body">
        {headline && (
          <>
            <p className="val">{headline.value ?? "—"}</p>
            <p className="sub">{headline.period ?? headline.label}</p>
          </>
        )}
        {current && <p className="body">{current.summary}</p>}
      </div>

      <RevisionRail
        revisionCount={card.revision_count}
        materialCount={card.material_count}
      />

      <div className="card-foot">
        <span className="upd">
          {card.last_updated_at ? `Updated ${card.last_updated_at}` : "Not yet built"}
        </span>
        {/* the routing binding from card_sources — "Updates from" */}
        <span className="src">
          from <b>{card.source_label}</b>
        </span>
      </div>
    </button>
  );
}
