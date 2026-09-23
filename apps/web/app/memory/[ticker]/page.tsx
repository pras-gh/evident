import { notFound } from "next/navigation";
import { MemoryDashboard } from "./MemoryDashboard";
import { getCards, getCompany, getCompanyTimeline } from "@/lib/api";

export const revalidate = 60;

export default async function MemoryPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;
  let data;
  try {
    data = await Promise.all([getCompany(ticker), getCards(ticker), getCompanyTimeline(ticker)]);
  } catch {
    notFound();
  }
  const [company, cards, timeline] = data;
  // the rail is for changes; filings are on the full timeline
  const recent = timeline.events.filter((e) => e.kind !== "filed").slice(0, 8);

  return <MemoryDashboard company={company} cards={cards} timeline={recent} />;
}
