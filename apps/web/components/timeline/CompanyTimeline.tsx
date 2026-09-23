import type {
  CompanyTimeline,
  TimelineEvent,
  TimelineFiling,
  TimelineKind,
} from "@/lib/types";

/**
 * What changed between a company's filings, newest first: a date column, a
 * rail, and one card per event. Every change card links to the paragraph that
 * shows it, opened in the evidence viewer.
 *
 * No client JavaScript. Category filters are links, so a filtered timeline has
 * a URL and the counts come from the API rather than from whatever happens to
 * be loaded.
 */
export function CompanyTimelineView({
  timeline,
  category,
}: {
  timeline: CompanyTimeline;
  category?: string | null;
}) {
  const ticker = timeline.ticker.toLowerCase();
  const byAccession = new Map(timeline.filings.map((f) => [f.accession, f]));
  const groups = groupByFiling(timeline.events);
  const all = Object.values(timeline.categories).reduce((a, b) => a + b, 0);
  const active = category?.toLowerCase() ?? null;

  return (
    <main className="tl2-shell">
      <header className="tl2-head">
        <p className="ev-kicker">{timeline.ticker} · Timeline</p>
        <h1 className="ev-title">{timeline.company}</h1>
        <p className="tl2-coverage" data-testid="coverage">
          {coverageNote(timeline)}
        </p>

        <nav className="tl2-filters" aria-label="Filter by category">
          <a href="?" className="tl2-filter" aria-current={active ? undefined : "page"}>
            All <span className="tl2-count">{all}</span>
          </a>
          {Object.entries(timeline.categories)
            .sort(([a], [b]) => categoryRank(a) - categoryRank(b) || a.localeCompare(b))
            .map(([name, n]) => (
              <a
                key={name}
                href={`?category=${encodeURIComponent(name.toLowerCase())}`}
                className={`tl2-filter ${tone(name)}`}
                aria-current={active === name.toLowerCase() ? "page" : undefined}
              >
                {name} <span className="tl2-count">{n}</span>
              </a>
            ))}
        </nav>
      </header>

      {groups.length === 0 ? (
        <p className="tl2-empty">No events{active ? ` in ${category}` : ""}.</p>
      ) : (
        <ol className="tl2" aria-label="Events, newest first">
          {groups.map(({ filing, events }) => {
            const [month, year] = events[0].date_label.split(" ");
            const info = byAccession.get(filing.accession);
            return (
              <li className="tl2-group" key={filing.accession}>
                <div className="tl2-date" aria-hidden>
                  <span className="tl2-month">{month}</span>
                  <span className="tl2-year">{year}</span>
                </div>
                <ol className="tl2-events" aria-label={filingLabel(filing)}>
                  {events.map((e) => (
                    <EventCard key={e.id} event={e} ticker={ticker} filing={info} />
                  ))}
                </ol>
              </li>
            );
          })}
        </ol>
      )}
    </main>
  );
}

function EventCard({
  event: e,
  ticker,
  filing,
}: {
  event: TimelineEvent;
  ticker: string;
  filing: TimelineFiling | undefined;
}) {
  const ev = e.evidence;
  const href =
    ev && e.topic && ev.chunk_id != null ? evidenceHref(ticker, e.topic.slug, ev) : null;
  return (
    <li className={`tl2-event ${tone(e.category)}`} data-kind={e.kind}>
      <span className="tl2-dot" aria-hidden />
      <article className="tl2-card" aria-label={e.title}>
        <span className="tl2-icon" aria-hidden>
          <CategoryIcon category={e.category} />
        </span>
        <div className="tl2-body">
          <p className="tl2-meta">
            <span className="tl2-cat">{e.category}</span>
            <span className={`tl2-kind k-${e.kind}`}>{KIND_LABEL[e.kind]}</span>
            <time dateTime={e.date}>{e.date_label}</time>
          </p>
          <h3 className="tl2-title">{e.title}</h3>
          <p className="tl2-summary">{e.summary}</p>
          {ev?.quote && <blockquote className="tl2-quote">{ev.quote}</blockquote>}

          <div className="tl2-foot">
            {ev &&
              (href ? (
                <a className="tl2-evidence" href={href} title="Open this paragraph in the filing">
                  <DocGlyph /> {ev.citation}
                  {ev.document_id !== e.filing.document_id && e.compared_with && (
                    <> · {e.compared_with.fiscal_period ?? e.compared_with.filed_at}</>
                  )}
                </a>
              ) : (
                <span className="tl2-evidence is-static">{ev.citation}</span>
              ))}
            {ev?.new_paragraph === true && (
              <span className="tl2-tag" title="No paragraph in the previous filing closely matches this one">
                new this filing
              </span>
            )}
            {e.kind === "filed" && filing && (
              <>
                {filing.coverage === "baseline" && (
                  <span className="tl2-tag is-mute" title="Nothing earlier to compare with">
                    baseline — nothing in it is marked new
                  </span>
                )}
                <a className="tl2-evidence" href={filing.url} target="_blank" rel="noreferrer">
                  <DocGlyph /> EDGAR {filing.accession}
                </a>
              </>
            )}
          </div>
        </div>
        {e.paragraphs != null && e.previous_paragraphs != null && e.kind !== "filed" && (
          <Delta before={e.previous_paragraphs} after={e.paragraphs} />
        )}
      </article>
    </li>
  );
}

function Delta({ before, after }: { before: number; after: number }) {
  const dir = after > before ? "up" : after < before ? "down" : "flat";
  return (
    <p
      className={`tl2-delta d-${dir}`}
      aria-label={`${after} paragraphs, ${before} in the previous filing`}
      title="Paragraphs citing this topic: previous filing → this filing"
    >
      {before} → {after}
      <span className="tl2-delta-unit">¶</span>
    </p>
  );
}

// --------------------------------------------------------------------------

export const KIND_LABEL: Record<TimelineKind, string> = {
  newly_disclosed: "New",
  disclosed_again: "Returned",
  expanded: "Expanded",
  narrowed: "Narrowed",
  no_longer_disclosed: "Dropped",
  filed: "Filed",
};

/** `/evidence/nvda/export_controls?cite=12:26_1`. Paragraph ids can carry
 *  `#` (`25_1#2`), which would otherwise start a URL fragment. */
export function evidenceHref(
  ticker: string,
  slug: string,
  ev: { chunk_id: number | null; paragraph_id: string | null },
): string {
  const cite = `${ev.chunk_id}${ev.paragraph_id ? `:${ev.paragraph_id}` : ""}`;
  return `/evidence/${encodeURIComponent(ticker)}/${encodeURIComponent(slug)}?cite=${encodeURIComponent(cite)}`;
}

function groupByFiling(events: TimelineEvent[]) {
  const out: { filing: TimelineEvent["filing"]; events: TimelineEvent[] }[] = [];
  for (const e of events) {
    const last = out[out.length - 1];
    if (last && last.filing.accession === e.filing.accession) last.events.push(e);
    else out.push({ filing: e.filing, events: [e] });
  }
  return out;
}

function filingLabel(f: TimelineEvent["filing"]): string {
  return `${f.fiscal_period ? `${f.fiscal_period} ` : ""}${f.form_type}, filed ${f.filed_at}`;
}

/** Says what was compared with what, so an empty or short timeline reads as
 *  "not enough history" rather than "nothing happened". */
export function coverageNote(t: CompanyTimeline): string {
  const byForm = new Map<string, TimelineFiling[]>();
  for (const f of t.filings) {
    if (f.coverage === "not compared") continue;
    byForm.set(f.form_type, [...(byForm.get(f.form_type) ?? []), f]);
  }
  if (byForm.size === 0) return "No periodic filings on record yet.";

  const parts = [...byForm.entries()].map(([form, fs]) => {
    const oldest = fs[fs.length - 1];
    const label = oldest.fiscal_period ?? oldest.filed_at;
    return fs.length === 1
      ? `one ${form} (${label}), so nothing to compare it with yet`
      : `${fs.length} ${form}s, each compared with the one before; the oldest, ${label}, is the baseline`;
  });
  const { min_delta, min_ratio } = t.thresholds;
  return (
    `On record: ${parts.join("; ")}. A topic is marked expanded when it is cited in ` +
    `at least ${min_delta} more paragraphs than in the previous filing of the same form, ` +
    `and at least ${min_ratio}× as many; narrowed is the reverse.`
  );
}

const CATEGORY_ORDER = ["Risk", "Product", "Strategy", "Geography", "Segment", "Metric",
  "Company", "Executive", "Filing"];

function categoryRank(c: string): number {
  const i = CATEGORY_ORDER.indexOf(c);
  return i === -1 ? CATEGORY_ORDER.length : i;
}

const TONE: Record<string, string> = {
  Risk: "t-red", Product: "t-indigo", Geography: "t-cyan", Strategy: "t-emerald",
  Executive: "t-amber", Metric: "t-teal", Segment: "t-violet", Company: "t-blue",
  Filing: "t-mute",
};

function tone(category: string): string {
  return TONE[category] ?? "t-mute";
}

const ICON_PATHS: Record<string, string[]> = {
  Risk: ["M12 3.5 2.8 19.5h18.4L12 3.5z", "M12 10v4", "M12 16.8v.1"],
  Product: ["M12 2.8 3.5 7.4v9.2l8.5 4.6 8.5-4.6V7.4L12 2.8z", "M3.5 7.4 12 12l8.5-4.6", "M12 12v9.2"],
  Geography: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M3 12h18", "M12 3c2.6 2.6 3.8 5.6 3.8 9s-1.2 6.4-3.8 9c-2.6-2.6-3.8-5.6-3.8-9S9.4 5.6 12 3z"],
  Strategy: ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7.5a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z", "M12 11.6v.8"],
  Executive: ["M12 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M4.5 20.5a7.5 7.5 0 0 1 15 0"],
  Metric: ["M4 20.5h16", "M7 17v-5", "M12 17V6", "M17 17v-8"],
  Segment: ["M12 3a9 9 0 1 0 9 9h-9V3z", "M15 3.5A9 9 0 0 1 20.5 9H15V3.5z"],
  Company: ["M5 20.5V4.5h9v16", "M14 9.5h5v11", "M3.5 20.5h17", "M8 8h2M8 11.5h2M8 15h2"],
  Filing: ["M14 3H6.5v18h11V6.5L14 3z", "M14 3v3.5h3.5", "M9 12h6M9 15.5h6"],
};

function CategoryIcon({ category }: { category: string }) {
  const paths = ICON_PATHS[category] ?? ICON_PATHS.Filing;
  return (
    <svg viewBox="0 0 24 24" className="ic" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
      {paths.map((d) => <path key={d} d={d} />)}
    </svg>
  );
}

function DocGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="ic-xs" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {ICON_PATHS.Filing.map((d) => <path key={d} d={d} />)}
    </svg>
  );
}
