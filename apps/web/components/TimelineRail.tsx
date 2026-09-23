import { evidenceHref } from "@/components/timeline/CompanyTimeline";
import type { TimelineEvent } from "@/lib/types";

/** The latest changes from the company timeline, each linked to its evidence. */
export function TimelineRail({ ticker, events }: { ticker: string; events: TimelineEvent[] }) {
  const t = ticker.toLowerCase();
  return (
    <section className="rail-card">
      <h3>Recent changes</h3>
      {events.length === 0 ? (
        <p className="tl-sub">
          Nothing to compare yet — changes appear once a second filing of the same form is
          ingested.
        </p>
      ) : (
        <ol className="tl">
          {events.map((e) => {
            const ev = e.evidence;
            const href =
              ev && e.topic && ev.chunk_id != null ? evidenceHref(t, e.topic.slug, ev) : null;
            return (
              <li className="tl-item tone-emerald" key={e.id}>
                <span className="tl-dot" aria-hidden />
                <p className="tl-date">
                  {e.date_label} · {e.category}
                </p>
                <p className="tl-title">
                  {href ? <a href={href}>{e.title}</a> : e.title}
                </p>
                <p className="tl-sub">{e.summary}</p>
              </li>
            );
          })}
        </ol>
      )}
      <a className="rail-more" href={`/timeline/${t}`}>
        Full timeline →
      </a>
    </section>
  );
}
