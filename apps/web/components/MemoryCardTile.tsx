"use client";

import type { MemoryCard } from "@/lib/types";
import { RevisionRail } from "./RevisionRail";

const TONE: Record<string, string> = {
  revenue: "emerald", ai: "indigo", products: "orange", guidance: "teal",
  risks: "red", capex: "cyan", capital: "cyan", promises: "amber", headcount: "blue",
  rd: "violet", litigation: "amber",
};

/** What a card's facts are, for cards whose facts are named things rather
 *  than numbers — the headline is then how many there are. */
const NOUN: Record<string, [string, string]> = {
  risks: ["risk factor", "risk factors"],
  litigation: ["matter", "matters"],
};

export function MemoryCardTile({
  card,
  onOpen,
}: {
  card: MemoryCard;
  onOpen: (kind: string) => void;
}) {
  const current = card.current;
  const valued = current?.facts.find((f) => f.value != null);
  const [one, many] = NOUN[card.kind] ?? ["item", "items"];

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
        {valued ? (
          <>
            <p className="val">{valued.value}</p>
            <p className="sub">{valued.period ?? valued.label}</p>
          </>
        ) : current ? (
          <>
            <p className="val">{current.facts.length}</p>
            <p className="sub">
              {current.facts.length === 1 ? one : many} disclosed
            </p>
          </>
        ) : null}
        {current && <p className="body">{current.summary}</p>}
        {!current && card.unavailable && <p className="body">{card.unavailable}</p>}
      </div>

      <RevisionRail
        revisionCount={card.revision_count}
        materialCount={card.material_count}
      />

      <div className="card-foot">
        <span className="upd">
          {card.last_updated_at
            ? `Updated ${card.last_updated_at}`
            : card.unavailable
              ? "No data yet"
              : "Not yet built"}
        </span>
        {/* the routing binding from card_sources — "Updates from" */}
        <span className="src">
          from <b>{card.source_label}</b>
        </span>
      </div>
    </button>
  );
}
