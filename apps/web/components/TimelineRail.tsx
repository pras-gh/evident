import type { TimelineEntry } from "@/lib/types";

export function TimelineRail({ entries }: { entries: TimelineEntry[] }) {
  return (
    <section className="rail-card">
      <h3>Recent timeline</h3>
      <ol className="tl">
        {entries.map((e) => (
          <li className={`tl-item tone-emerald`} key={e.ref + e.occurred_at}>
            <span className="tl-dot" aria-hidden />
            <p className="tl-date">{e.occurred_at}</p>
            <p className="tl-title">{e.headline}</p>
            <p className="tl-sub">{e.kind}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
