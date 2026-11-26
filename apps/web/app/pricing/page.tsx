import type { Metadata } from "next";
import { AppHeader } from "@/components/brand/AppHeader";
import { PlanCards } from "@/components/billing/PlanCards";
import { getPlans, getSubscription, getViewer } from "@/lib/api";

export const metadata: Metadata = {
  title: "Pricing — Parallx",
  description: "Two companies free. Iix for investors, IIx for analysts.",
};
export const dynamic = "force-dynamic";

type Search = Promise<{ interval?: string; checkout?: string }>;

const SOON = "Coming soon";
/** Free · Iix · IIx, as the product delivers them today. */
const COMPARE: [string, string, string, string][] = [
  ["Companies", "2 free, for good", "10 at a time", "50 at a time"],
  ["AI research queries", "10 to start", "100 a month", "500 a month"],
  ["SEC filing intelligence", "Included", "Included", "Included"],
  ["Historical company timelines", "Included", "Included", "Included"],
  ["Source-linked evidence viewer", "Included", "Included", "Included"],
  ["Financial statements from XBRL", "Included", "Included", "Included"],
  ["Management Promise Tracker", SOON, SOON, SOON],
  ["Weekly change alerts", "—", SOON, SOON],
  ["Daily change monitoring", "—", "—", SOON],
  ["Advanced commitment comparisons", "—", "—", SOON],
  ["Exportable research reports", "—", "—", SOON],
  ["Earnings transcript intelligence", "—", "—", SOON],
];

export default async function Pricing({ searchParams }: { searchParams: Search }) {
  const sp = await searchParams;
  const [plans, viewer] = await Promise.all([getPlans().catch(() => []), getViewer()]);
  const sub = viewer ? await getSubscription() : null;
  return (
    <>
      <AppHeader active="pricing" />
      <main className="pricing">
        <header className="pricing-head">
          <p className="eyebrow">Pricing</p>
          <h1>Research the companies you own, with the evidence attached.</h1>
          <p className="pricing-lede">
            Start with two companies, free — no card needed. Upgrade when you want more.
          </p>
          {sp.checkout === "canceled" && (
            <p className="plans-note" role="status">Checkout was cancelled. Nothing was charged.</p>
          )}
        </header>

        {plans.length ? (
          <PlanCards plans={plans} signedIn={!!viewer}
                     current={sub && sub.source !== "free" ? sub.plan_id : null}
                     initialInterval={sp.interval === "year" ? "year" : "month"} />
        ) : (
          <p className="plans-note">Plans can&rsquo;t be loaded right now. Try again shortly.</p>
        )}

        <section className="compare" aria-labelledby="compare-h">
          <h2 id="compare-h">Compare plans</h2>
          <div className="compare-scroll">
            <table>
              <thead>
                <tr><th scope="col"><span className="sr-only">Feature</span></th>
                    <th scope="col">Free</th><th scope="col">Iix</th>
                    <th scope="col" className="is-rec">IIx</th></tr>
              </thead>
              <tbody>
                {COMPARE.map(([label, ...cells]) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    {cells.map((c, i) => (
                      <td key={i} className={c === SOON ? "is-soon" : c === "—" ? "is-none" : undefined}>
                        {c}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="terms-box" aria-labelledby="terms-h">
          <h2 id="terms-h">Billing, plainly</h2>
          <dl>
            <div><dt>How you pay</dt><dd>By card, through Stripe&rsquo;s secure checkout, in US
              dollars. Parallx never sees or stores your card number.</dd></div>
            <div><dt>Monthly or annual</dt><dd>Monthly renews each month. Annual is charged once a
              year and costs ten months&rsquo; worth — two months free.</dd></div>
            <div><dt>Cancelling</dt><dd>Cancel anytime from your account. Your plan runs to the
              end of the period you paid for, then doesn&rsquo;t renew.</dd></div>
            <div><dt>Changing plans</dt><dd>Moving between Iix and IIx takes effect straight away;
              Stripe prorates the difference.</dd></div>
            <div><dt>If a payment fails</dt><dd>Stripe retries it. Your plan keeps working for 7
              days after the period ends; if it still hasn&rsquo;t gone through, the account
              returns to Free.</dd></div>
            <div><dt>Your research stays yours</dt><dd>On any plan, and after one ends, every
              company you&rsquo;ve researched stays open to you.</dd></div>
            <div><dt>India</dt><dd>Billing from India will be in rupees and isn&rsquo;t open yet;
              until it is, the free plan is available in full.</dd></div>
          </dl>
          <p className="terms-links">
            <a href="/terms">Terms of service</a> · <a href="/privacy">Privacy policy</a>
          </p>
        </section>
      </main>
    </>
  );
}
