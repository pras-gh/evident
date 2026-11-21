import { evidenceUrl } from "@/lib/cite";
import type { SearchHit, SearchOutcome } from "@/lib/types";

/**
 * Search over the filings: a box, and the passages nearest the question, each
 * opening the evidence viewer at the paragraph it came from.
 *
 * No client JavaScript. The form is a GET to /search, so a search has a URL
 * that can be shared, reloaded and linked to from the landing page.
 */
export function SearchView({
  query,
  ticker,
  outcome,
}: {
  query: string;
  ticker?: string | null;
  outcome: SearchOutcome | null;
}) {
  return (
    <main className="tl2-shell">
      <header className="tl2-head">
        <p className="ev-kicker">{ticker ? `${ticker.toUpperCase()} · Search` : "Search"}</p>
        <h1 className="ev-title">Search the filings</h1>
        <form className="srch-form" action="/search" method="get" role="search">
          <label className="srch-label" htmlFor="q">Search the filings</label>
          <input id="q" name="q" type="search" defaultValue={query} minLength={2} required
                 autoComplete="off" spellCheck={false}
                 placeholder="e.g. restrictions on selling chips to China" />
          {ticker && <input type="hidden" name="ticker" value={ticker} />}
          <button type="submit">Search</button>
        </form>
        <p className="tl2-coverage">
          Finds passages by meaning, not exact words. Each result opens the filing at the
          paragraph it came from.
        </p>
      </header>

      {outcome === null ? (
        <Examples ticker={ticker} />
      ) : !outcome.ok ? (
        <p className="srch-note" role="alert">
          <b>{HEADLINE[outcome.error]}</b> {outcome.message}
        </p>
      ) : outcome.result.hits.length === 0 ? (
        <p className="tl2-empty">No passages found for “{outcome.result.query}”.</p>
      ) : (
        <>
          <p className="srch-count" aria-live="polite">
            {outcome.result.hits.length} passage{outcome.result.hits.length === 1 ? "" : "s"}
            {" "}· {Math.round(outcome.result.took_ms)} ms
          </p>
          <ol className="srch-hits" aria-label={`Results for ${outcome.result.query}`}>
            {outcome.result.hits.map((h) => <Hit key={h.chunk_id} hit={h} />)}
          </ol>
        </>
      )}
    </main>
  );
}

const HEADLINE: Record<Exclude<SearchOutcome, { ok: true }>["error"], string> = {
  search_not_configured: "Search isn’t set up.",
  search_not_indexed: "Nothing is indexed yet.",
  search_unavailable: "The search provider didn’t answer. Try again in a minute.",
  too_short: "That query is too short.",
  unreachable: "Search is unavailable.",
  failed: "Search failed.",
};

/** Examples that the demo corpus — NVIDIA's Risk Factors — can answer. */
const EXAMPLES = [
  "restrictions on selling chips to China",
  "we rely on one company in Taiwan to make our products",
  "laws about climate change",
  "hackers breaking into our systems",
];

function Examples({ ticker }: { ticker?: string | null }) {
  return (
    <ul className="tl2-filters" aria-label="Example searches">
      {EXAMPLES.map((q) => {
        const params = new URLSearchParams({ q, ...(ticker ? { ticker } : {}) });
        return (
          <li key={q}>
            <a className="tl2-filter" href={`/search?${params}`}>{q}</a>
          </li>
        );
      })}
    </ul>
  );
}

function Hit({ hit }: { hit: SearchHit }) {
  return (
    <li className="tl2-card srch-hit">
      <div>
        <p className="tl2-meta">
          <span className="tl2-kind">{hit.form_type}</span>
          <span>{filedLabel(hit.filed_at)}</span>
          <span>{hit.citation}</span>
        </p>
        <p className="srch-text">{excerpt(hit.text)}</p>
        <p className="tl2-foot">
          <a className="tl2-evidence" href={evidenceUrl(hit.chunk_id, hit.paragraph_ids[0] ?? null)}>
            Open in the filing →
          </a>
          <span className="tl2-tag is-mute" title="Cosine similarity to the query">
            {Math.round(hit.score * 100)}% match
          </span>
        </p>
      </div>
    </li>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-02-25" → "filed Feb 2026", without a timezone shifting the day. */
export function filedLabel(iso: string): string {
  const [y, m] = iso.split("-");
  return `filed ${MONTHS[Number(m) - 1] ?? ""} ${y}`.replace("  ", " ");
}

/** The passage cut at a word near `max` characters. */
export function excerpt(text: string, max = 420): string {
  const flat = text.replace(/\s+/g, " ").trim();
  if (flat.length <= max) return flat;
  const cut = flat.lastIndexOf(" ", max);
  return `${flat.slice(0, cut > max * 0.6 ? cut : max)}…`;
}
