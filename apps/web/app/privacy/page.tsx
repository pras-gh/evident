import type { Metadata } from "next";
import { AppHeader } from "@/components/brand/AppHeader";

export const metadata: Metadata = { title: "Privacy — Parallx" };

export default function Privacy() {
  return (
    <>
      <AppHeader />
      <main className="doc">
        <h1>Privacy policy</h1>
        <p className="doc-draft" role="note">
          This page describes what Parallx stores today. The full privacy policy will replace it
          before launch.
        </p>
        <h2>Signing in</h2>
        <p>Sign-in is handled by Google Firebase Authentication. Parallx receives your name, your
          email address or phone number, and a sign-in identifier — never your password or a
          one-time code.</p>
        <h2>What Parallx keeps</h2>
        <ul>
          <li>Your account: name, email or phone, and how you signed in.</li>
          <li>Your sessions: a hashed session token, when it expires, and your browser&rsquo;s
            user agent.</li>
          <li>Your research: the companies you&rsquo;ve researched and your usage of AI research
            queries.</li>
          <li>Billing: your Stripe customer and subscription identifiers. Card details go to
            Stripe directly and never reach Parallx.</li>
        </ul>
        <h2>Questions you ask</h2>
        <p>AI research questions are sent to the AI providers Parallx uses to answer them.</p>
      </main>
    </>
  );
}
