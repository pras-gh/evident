import { AppHeader } from "@/components/brand/AppHeader";
import type { Metadata } from "next";
import { Suspense } from "react";
import { AnswerCard, AnswerPending } from "@/components/search/Answer";
import { SearchView } from "@/components/search/SearchView";
import { getCompanyTimeline, searchFilings } from "@/lib/api";

// a search is per request; caching a page keyed on its query string would only
// ever serve a stale index
export const dynamic = "force-dynamic";

type Search = Promise<{ q?: string | string[]; ticker?: string | string[] }>;

const first = (v?: string | string[]) => (Array.isArray(v) ? v[0] : v) ?? "";

export async function generateMetadata({ searchParams }: { searchParams: Search }): Promise<Metadata> {
  const q = first((await searchParams).q).trim();
  return { title: q ? `“${q}” — Search — Parallx` : "Search — Parallx" };
}

/** Search over every filing on record, or one company's with `?ticker=`. */
export default async function SearchPage({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  const query = first(params.q).trim();
  const ticker = first(params.ticker).trim() || null;
  const outcome = query ? await searchFilings(query, ticker ?? undefined) : null;

  // A question about one company we cover gets its card; a missing timeline
  // only costs the card, never the results.
  const scope = outcome?.ok ? outcome.result.scope : null;
  const one = scope?.reason === "named" && scope.tickers?.length === 1 ? scope.tickers[0] : null;
  const company = one ? await getCompanyTimeline(one).catch(() => null) : null;

  // The answer streams in after the results: it can take ten seconds or so.
  // It gets the passages search found, so nothing is searched twice.
  const result = outcome?.ok ? outcome.result : null;
  const answer = result && (result.hits.length > 0 || result.kind === "market"
                            || result.kind === "general") ? (
    <Suspense fallback={<AnswerPending />}>
      <AnswerCard query={query} kind={result.kind}
                  chunkIds={result.hits.slice(0, 12).map((h) => h.chunk_id)} />
    </Suspense>
  ) : null;

  // the company in view, for the header's sections: the one the query named,
  // else the page's filter — unless the query named other companies
  const inView = one ?? (scope?.reason === "named" ? null : ticker);
  return (
    <>
      <AppHeader ticker={inView} active="search" />
      <SearchView query={query} ticker={ticker} outcome={outcome} company={company}
                  answer={answer} />
    </>
  );
}
