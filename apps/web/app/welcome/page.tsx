import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ParallxMark } from "@/components/brand/ParallxMark";
import { SearchBox } from "@/components/auth/SearchBox";
import { Continue } from "@/components/onboarding/Continue";
import { safeNext } from "@/lib/next-path";
import { getUsage, getViewer } from "@/lib/api";

export const metadata: Metadata = { title: "Welcome — Parallx" };
export const dynamic = "force-dynamic";

type Search = Promise<{ next?: string }>;

/** First sign-in: what they have, and on to what they came for. */
export default async function Welcome({ searchParams }: { searchParams: Search }) {
  const viewer = await getViewer();
  if (!viewer) redirect("/signin");
  const usage = await getUsage();
  const next = safeNext((await searchParams).next, "/");
  const q = next.startsWith("/search") ? new URL(next, "http://x").searchParams.get("q") : null;
  const total = usage?.free_credits_total ?? 2;
  const left = usage ? usage.free_credits_total - usage.free_credits_used : total;
  const first = viewer.name?.split(" ")[0];

  return (
    <main className="welcome">
      <span className="welcome-mark" aria-hidden><ParallxMark size={28} /></span>
      <p className="eyebrow">{first ? `Welcome, ${first}` : "Welcome to Parallx"}</p>
      <h1>Your research starts here.</h1>
      <p className="welcome-sub">
        You have {total} free company research credits. No payment required.
      </p>
      <ol className="credits" aria-label={`${left} of ${total} credits left`}>
        {Array.from({ length: total }, (_, i) => (
          <li key={i} className={i < left ? "is-left" : "is-used"}>
            <span className="credit-n">{i + 1}</span>
            <span className="credit-l">{i < left ? "Free company" : "Used"}</span>
          </li>
        ))}
      </ol>
      <p className="welcome-how">
        A credit is used the first time you research a company. Coming back to it — or
        searching it again — is always free.
      </p>
      {q ? (
        <Continue href={next} label={`Continue to “${q}”`} />
      ) : next !== "/" ? (
        <Continue href={next} label="Continue" />
      ) : (
        <div className="welcome-search">
          <p className="welcome-cta-label">Research your first company</p>
          <SearchBox signedIn autoFocus placeholder="Nvidia, AAPL, Microsoft…" />
        </div>
      )}
    </main>
  );
}
