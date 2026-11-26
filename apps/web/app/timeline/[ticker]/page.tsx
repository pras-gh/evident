import { AppHeader } from "@/components/brand/AppHeader";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CompanyTimelineView } from "@/components/timeline/CompanyTimeline";
import { getCompanyTimeline } from "@/lib/api";
import { researchGate } from "@/components/billing/ResearchGate";

export const dynamic = "force-dynamic";

type Params = Promise<{ ticker: string }>;
type Search = Promise<{ category?: string | string[] }>;

export async function generateMetadata({ params }: { params: Params }): Promise<Metadata> {
  const { ticker } = await params;
  return { title: `Timeline · ${ticker.toUpperCase()} — Parallx` };
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
  const gate = await researchGate(ticker, `/timeline/${ticker}`);
  if (gate.view) return gate.view;
  if (!gate.outcome.ok) notFound();
  const raw = (await searchParams).category;
  const category = (Array.isArray(raw) ? raw[0] : raw) || undefined;

  let timeline;
  try {
    timeline = await getCompanyTimeline(ticker, category);
  } catch {
    notFound();
  }
  return (
    <>
      <AppHeader ticker={ticker} active="timeline" />
      <CompanyTimelineView timeline={timeline} category={category} />
    </>
  );
}
