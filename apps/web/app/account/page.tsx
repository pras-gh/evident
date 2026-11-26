import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AccountActions } from "@/components/account/AccountActions";
import { AppHeader } from "@/components/brand/AppHeader";
import { UsageMeter } from "@/components/billing/UsageMeter";
import { getSubscription, getUsage, getViewer } from "@/lib/api";

export const metadata: Metadata = { title: "Your account — Parallx" };
export const dynamic = "force-dynamic";

const day = (iso: string | null) => iso
  ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })
  : null;

export default async function Account() {
  const viewer = await getViewer();
  if (!viewer) redirect("/signin?next=/account");
  const [usage, sub] = await Promise.all([getUsage(), getSubscription()]);
  const paid = sub?.source === "subscription" || sub?.source === "grace";
  return (
    <>
      <AppHeader active="account" />
      <main className="acct">
        <h1>Your account</h1>
        <p className="acct-who">
          {viewer.name && <b>{viewer.name}</b>} {viewer.email ?? viewer.phone}
          <span className="acct-via">
            {viewer.auth_provider === "google" ? " · Google" : viewer.auth_provider === "phone"
              ? " · Phone" : viewer.auth_provider === "password" ? " · Email" : ""}
          </span>
        </p>

        <section className="acct-card" aria-labelledby="plan-h">
          <h2 id="plan-h">Plan</h2>
          <p className="acct-plan">
            <b>{sub?.plan_name ?? "Free"}</b>
            {paid && sub?.billing_interval && <span> · billed {sub.billing_interval === "year" ? "yearly" : "monthly"}</span>}
          </p>
          {sub?.source === "grace" && (
            <p className="acct-warn" role="alert">
              Your last payment didn&rsquo;t go through. Update your card by {day(sub.grace_until)} to
              keep {sub.plan_name}; after that the account returns to Free.
            </p>
          )}
          {sub?.requires_action && (
            <p className="acct-warn" role="alert">
              Your bank needs you to confirm a payment. Open Manage billing to finish it.
            </p>
          )}
          {paid && sub?.cancel_at_period_end && (
            <p className="acct-note">Cancelled — your plan runs until {day(sub.current_period_end)}.</p>
          )}
          {paid && !sub?.cancel_at_period_end && sub?.current_period_end && (
            <p className="acct-note">Renews {day(sub.current_period_end)}.</p>
          )}
          {!paid && (
            <p className="acct-note">
              Two companies free, for good. <a href="/pricing">See Iix and IIx</a> to research more.
            </p>
          )}
          <AccountActions hasBilling={!!sub?.has_billing_account}
                          canCancel={paid && !sub?.cancel_at_period_end}
                          periodEnd={sub?.current_period_end ?? null} />
        </section>

        {usage && (
          <section className="acct-card" aria-labelledby="use-h">
            <h2 id="use-h">Usage</h2>
            <UsageMeter usage={usage} />
            {usage.ai_queries_limit !== null && (
              <p className="acct-note">
                {usage.ai_queries_used} of {usage.ai_queries_limit} AI research queries
                {usage.source === "free" ? " to start" : ` this period${usage.period_end
                  ? `, until ${day(usage.period_end)}` : ""}`}.
              </p>
            )}
          </section>
        )}

        {usage && usage.companies.length > 0 && (
          <section className="acct-card" aria-labelledby="co-h">
            <h2 id="co-h">Your companies</h2>
            <ul className="acct-cos">
              {usage.companies.map((c) => c.ticker && (
                <li key={c.ticker}>
                  <a href={`/memory/${c.ticker.toLowerCase()}`}>
                    <span className="dir-tk">{c.ticker}</span> {c.name}
                  </a>
                  <span className="acct-since">since {day(c.first_researched_at)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
    </>
  );
}
