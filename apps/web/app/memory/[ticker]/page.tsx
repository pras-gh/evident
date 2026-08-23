import { MemoryDashboard } from "./MemoryDashboard";
import { getCards, getCompany, getTimeline } from "@/lib/api";

export const revalidate = 60;

export default async function MemoryPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;
  const [company, cards, timeline] = await Promise.all([
    getCompany(ticker),
    getCards(ticker),
    getTimeline(ticker),
  ]);

  return <MemoryDashboard company={company} cards={cards} timeline={timeline} />;
}
