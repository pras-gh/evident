import type { ResolvedCitation } from "@/lib/types";

/**
 * One citation: its number, where it is, and how sure extraction was.
 *
 * An unresolvable citation stays in place, disabled, so chip 3 is always
 * citation 3 — dropping it would renumber everything after it.
 */
export function CitationChip({
  n,
  citation,
  active,
  showFiling,
  onSelect,
}: {
  n: number;
  citation: ResolvedCitation;
  active: boolean;
  showFiling: boolean;
  onSelect: () => void;
}) {
  const ev = citation.evidence;
  if (!ev) {
    return (
      <button type="button" className="chip is-unresolved" disabled
        title={citation.error ?? "could not be resolved"}>
        <span className="chip-n">{n}</span> unavailable
      </button>
    );
  }
  const filing = ev.fiscal_period ?? ev.filed_at.slice(0, 4);
  return (
    <button
      type="button"
      className={`chip${active ? " is-active" : ""}`}
      style={{ ["--hl" as string]: ev.highlight_color }}
      aria-pressed={active}
      title={ev.citation}
      onClick={onSelect}
    >
      <span className="chip-n">{n}</span>
      {showFiling && <span className="chip-filing">{filing}</span>}
      <span className="chip-page">p.&nbsp;{ev.page ?? "—"}</span>
    </button>
  );
}
