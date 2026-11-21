import { evidenceUrl } from "@/lib/cite";
import { FetchWatcher } from "./FetchWatcher";
import type {
  CompanyTimeline,
  SearchCompany,
  SearchHit,
  SearchOutcome,
  SearchResponse,
} from "@/lib/types";

/**
 * Search over the filings: a box, and the passages nearest the question, each
 * opening the evidence viewer at the paragraph it came from.
 *
 * No client JavaScript. The form is a GET to /search, so a search has a URL
 * that can be shared, reloaded and linked to from the landing page.
 *
 * Search is scoped to the companies the query names, and the page says which
 * it searched and why — so "Wesco International" says Evident has no Wesco
 * filings rather than showing NVIDIA's. A query about one company we cover
 * also gets a card: what its latest filing changed, and where to read more.
 */
export function SearchView({
  query,
  ticker,
  outcome,
  company,
}: {
  query: string;
  ticker?: string | null;
  outcome: SearchOutcome | null;
  /** the timeline of the one company the query names, for its card */
  company?: CompanyTimeline | null;
}) {
  // the page's company filter, unless the query named companies of its own
  const filtered = !!ticker && !(outcome?.ok && outcome.result.scope?.reason === "named");
  return (
    <main className="tl2-shell">
      <header className="tl2-head">
        <p className="ev-kicker">{filtered ? `${ticker!.toUpperCase()} · Search` : "Search"}</p>
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
      ) : (
        <>
          <Scope result={outcome.result} />
          {company && <CompanyCard timeline={company} />}
          <Results result={outcome.result} />
        </>
      )}
    </main>
  );
}

function Results({ result }: { result: SearchResponse }) {
  // nothing searched: the scope notice already says why
  if (result.scope?.tickers?.length === 0) return null;
  if (result.hits.length === 0)
    return <p className="tl2-empty">No passages found for “{result.query}”.</p>;
  return (
    <>
      <p className="srch-count" aria-live="polite">
        {result.hits.length} passage{result.hits.length === 1 ? "" : "s"}
        {" "}· {Math.round(result.took_ms)} ms
      </p>
      <ol className="srch-hits" aria-label={`Results for ${result.query}`}>
        {result.hits.map((h) => <Hit key={h.chunk_id} hit={h} />)}
      </ol>
    </>
  );
}

const label = (c: SearchCompany) => (c.ticker ? `${c.name} (${c.ticker})` : c.name);
const names = (cs: SearchCompany[]) => cs.map(label).join(", ");

/** Which companies were searched, and why — in words. */
export function Scope({ result }: { result: SearchResponse }) {
  const uncovered = result.companies.filter((c) => !c.covered);
  const covered = result.companies.filter((c) => c.covered);
  const fetching = uncovered.filter((c) => c.fetch === "queued" || c.fetch === "running");
  const failed = uncovered.filter((c) => c.fetch === "failed");
  const offEdgar = uncovered.filter((c) => c.fetch === "not_on_edgar");
  const missing = uncovered.filter((c) => !c.fetch || c.fetch === "not_configured");
  const unconfigured = uncovered.some((c) => c.fetch === "not_configured");
  const onRecord = result.on_record.length ? names(result.on_record) : "no company yet";
  const reason = result.scope?.reason;
  const about = result.topic ? <> about “{result.topic}”</> : null;
  return (
    <div className="srch-scope" data-testid="scope">
      {fetching.length > 0 && (
        <p className="srch-note is-busy" role="status">
          <b>Fetching {names(fetching)} from the SEC.</b> Its latest annual report is being
          downloaded and indexed, which usually takes a minute or two; this page updates
          when it’s ready.{" "}
          <FetchWatcher ids={fetching.map((c) => c.fetch_id!).filter(Boolean)} />
        </p>
      )}
      {failed.map((c) => (
        <p className="srch-note" role="status" key={c.fetch_id ?? c.name}>
          <b>Couldn’t fetch {label(c)} from the SEC.</b> {c.fetch_error ?? ""}
        </p>
      ))}
      {offEdgar.length > 0 && (
        <p className="srch-note" role="status">
          <b>{names(offEdgar)} {offEdgar.length === 1 ? "doesn’t" : "don’t"} file with the
          SEC</b>, so Evident has no filings to search. Evident has filings for {onRecord}.
        </p>
      )}
      {missing.length > 0 && (
        <p className="srch-note" role="status">
          <b>No filings for {names(missing)} yet.</b> Evident has filings for {onRecord}.
          {unconfigured && <> Fetching them needs <code>SEC_USER_AGENT</code> set, with a
          contact the SEC accepts.</>}
        </p>
      )}
      {reason === "named" && covered.length > 0 && (
        <p className="srch-scope-line">Showing filings from <b>{names(covered)}</b>{about}.</p>
      )}
      {reason === "filter" && (
        <p className="srch-scope-line">
          Showing <b>{result.scope?.tickers?.join(", ")}</b> filings{about}.{" "}
          <a href={`/search?${new URLSearchParams({ q: result.query })}`}>Search every company</a>
        </p>
      )}
      {reason === "all" && (
        <p className="srch-scope-line">
          {result.understood_by === "rules"
            ? "Couldn’t check which company you meant, so this searches"
            : "Your search names no company, so it searches"}{" "}
          every company on record: {onRecord}.
        </p>
      )}
    </div>
  );
}

/** One company at a glance: its filings, and what the latest one changed. */
export function CompanyCard({ timeline }: { timeline: CompanyTimeline }) {
  const t = timeline.ticker.toLowerCase();
  const latest = timeline.filings[0];
  const changes = timeline.events
    .filter((e) => e.kind !== "filed" && e.topic && e.filing.accession === latest?.accession)
    .slice(0, 5);
  return (
    <section className="tl2-card srch-company" aria-label={`${timeline.company} at a glance`}>
      <div>
        <p className="tl2-meta">
          <span className="tl2-kind">{timeline.ticker}</span>
          <span>
            {timeline.filings.length} filing{timeline.filings.length === 1 ? "" : "s"} on record
            {latest ? `, latest ${latest.form_type} ${filedLabel(latest.filed_at)}` : ""}
          </span>
        </p>
        <h2 className="tl2-title">{timeline.company}</h2>
        {changes.length > 0 && (
          <>
            <p className="tl2-summary">What changed in the latest filing</p>
            <ul className="srch-changes">
              {changes.map((e) => (
                <li key={e.id}>
                  {e.evidence?.chunk_id ? (
                    <a href={evidenceUrl(e.evidence.chunk_id, e.evidence.paragraph_id,
                                         e.topic?.slug)}>{e.title}</a>
                  ) : (
                    e.title
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="tl2-foot">
          <a className="tl2-evidence" href={`/memory/${t}`}>Company memory →</a>
          <a className="tl2-evidence" href={`/timeline/${t}`}>Full timeline →</a>
        </p>
      </div>
    </section>
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
