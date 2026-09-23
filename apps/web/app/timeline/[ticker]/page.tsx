import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CompanyTimelineView } from "@/components/timeline/CompanyTimeline";
import { getCompanyTimeline } from "@/lib/api";

export const revalidate = 60;

type Params = Promise<{ ticker: string }>;
type Search = Promise<{ category?: string | string[] }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { ticker } = await params;
  return { title: `Timeline · ${ticker.toUpperCase()} — Evident` };
}

/** What changed between a company's filings, each change linked to the
 *  paragraph that shows it. */
export default async function TimelinePage({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Search;
}) {
  const { ticker } = await params;
  const raw = (await searchParams).category;
  const category = (Array.isArray(raw) ? raw[0] : raw) || undefined;

  let timeline;
  try {
    timeline = await getCompanyTimeline(ticker, category);
  } catch {
    notFound();
  }
  return <CompanyTimelineView timeline={timeline} category={category} />;
}
