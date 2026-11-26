import { notFound } from "next/navigation";
import { MemoryDashboard } from "./MemoryDashboard";
import { researchGate } from "@/components/billing/ResearchGate";
import { getCards, getCompany, getCompanyFilings, getCompanyTimeline,
         getFinancials } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function MemoryPage({
  params,
  searchParams,
}: {
  params: Promise<{ ticker: string }>;
  searchParams: Promise<{ period?: string }>;
}) {
  const { ticker } = await params;
  const period = (await searchParams).period === "quarterly" ? "quarterly" : "annual";
  // researching it spends a credit the first time; the gate shows the upgrade,
  // or the filings on their way, when the page cannot render yet
  const gate = await researchGate(ticker, `/memory/${ticker}`);
  if (gate.view) return gate.view;
  if (!gate.outcome.ok) notFound();
  let data;
  try {
    data = await Promise.all([getCompany(ticker), getCards(ticker), getCompanyTimeline(ticker)]);
  } catch {
    notFound();
  }
  const [company, cards, timeline] = data;
  // statements and the filing index are their own sources: a failure there
  // leaves the rest of the page standing
  const [financials, filings] = await Promise.all([
    getFinancials(ticker, period).catch(() => null),
    getCompanyFilings(ticker, 12).catch(() => []),
  ]);
  // the rail is for changes; filings are on the full timeline
  const recent = timeline.events.filter((e) => e.kind !== "filed").slice(0, 8);

  return (
    <MemoryDashboard company={company} cards={cards} timeline={recent}
                     financials={financials} filings={filings} />
  );
}
