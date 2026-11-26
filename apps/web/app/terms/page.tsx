import type { Metadata } from "next";
import { AppHeader } from "@/components/brand/AppHeader";

export const metadata: Metadata = { title: "Terms — Parallx" };

export default function Terms() {
  return (
    <>
      <AppHeader />
      <main className="doc">
        <h1>Terms of service</h1>
        <p className="doc-draft" role="note">
          This page summarises how Parallx works today. The full terms of service will replace
          it before launch.
        </p>
        <h2>The service</h2>
        <p>Parallx is a research tool. It finds and cites what public companies have filed with
          the SEC. It is not investment advice, and the judgement stays yours.</p>
        <h2>Accounts</h2>
        <p>One account per person. Free accounts include two company research credits for good;
          a credit is used the first time you research a company, and never again for it.</p>
        <h2>Subscriptions</h2>
        <p>Iix ($19 a month or $190 a year) and IIx ($39 a month or $390 a year) are billed in US
          dollars by Stripe, and renew until cancelled. Cancel anytime from your account: the plan
          runs to the end of the period paid for, then doesn&rsquo;t renew. If a payment fails,
          the plan keeps working for 7 days after the period ends while Stripe retries it, then the
          account returns to Free. Companies you&rsquo;ve researched stay open on any plan.</p>
        <p><a href="/pricing">Pricing and billing details</a></p>
      </main>
    </>
  );
}
