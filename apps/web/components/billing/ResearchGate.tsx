import { AppHeader } from "@/components/brand/AppHeader";
import { FetchWatcher } from "@/components/search/FetchWatcher";
import { getPlans, getSubscription, researchAccess, type AccessOutcome } from "@/lib/api";
import type { Paywall } from "@/lib/types";
import { UpgradeModal } from "./UpgradeModal";

/**
 * Before a company page renders its research: start researching the company
 * (a credit, the first time). Returns null when the page may render, or what
 * to show instead — the upgrade, or a company whose filings are on their way.
 */
export async function researchGate(ticker: string, returnTo: string):
    Promise<{ outcome: AccessOutcome; view: React.ReactNode | null }> {
  const outcome = await researchAccess(ticker);
  if (outcome.ok && outcome.access.status === "pending" && outcome.access.fetch_id) {
    return { outcome, view: (
      <>
        <AppHeader ticker={ticker} />
        <main className="gate">
          <span className="spinner" aria-hidden />
          <h1>Fetching {outcome.access.name}&rsquo;s filings</h1>
          <p>Its latest annual report is coming from the SEC and being read. This takes about a
            minute; the page opens when it&rsquo;s ready. If it can&rsquo;t be fetched, your
            credit comes back.</p>
          <FetchWatcher ids={[outcome.access.fetch_id]} />
        </main>
      </>
    ) };
  }
  if (!outcome.ok && outcome.status === 402 && outcome.body) {
    const [plans, sub] = await Promise.all([getPlans().catch(() => []), getSubscription()]);
    const b = outcome.body;
    const paywall: Paywall = { reason: b.reason, message: b.message, usage: b.usage ?? null,
                               companies: [b.name ?? ticker.toUpperCase()] };
    return { outcome, view: (
      <>
        <AppHeader />
        <main className="gate">
          <p className="eyebrow">{b.name ?? ticker.toUpperCase()}</p>
          <h1>Research {b.name ?? ticker.toUpperCase()} with a plan</h1>
          <p>{b.message} Companies you&rsquo;ve researched stay open.</p>
          <a className="btn-ghost" href="/">Back to your companies</a>
          <UpgradeModal plans={plans} paywall={paywall} returnTo={returnTo}
                        current={sub && sub.source !== "free" ? sub.plan_id : null} />
        </main>
      </>
    ) };
  }
  return { outcome, view: null };
}
