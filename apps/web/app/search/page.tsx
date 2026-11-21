import type { Metadata } from "next";
import { SearchView } from "@/components/search/SearchView";
import { getCompanyTimeline, searchFilings } from "@/lib/api";

// a search is per request; caching a page keyed on its query string would only
// ever serve a stale index
export const dynamic = "force-dynamic";

type Search = Promise<{ q?: string | string[]; ticker?: string | string[] }>;

const first = (v?: string | string[]) => (Array.isArray(v) ? v[0] : v) ?? "";

export async function generateMetadata({ searchParams }: { searchParams: Search }): Promise<Metadata> {
  const q = first((await searchParams).q).trim();
  return { title: q ? `“${q}” — Search — Evident` : "Search — Evident" };
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

  return <SearchView query={query} ticker={ticker} outcome={outcome} company={company} />;
}
