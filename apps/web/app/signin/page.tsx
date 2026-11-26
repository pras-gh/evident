import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AuthCard } from "@/components/auth/AuthCard";
import { safeNext } from "@/lib/next-path";
import { getViewer } from "@/lib/api";

export const metadata: Metadata = { title: "Sign in — Parallx" };
export const dynamic = "force-dynamic";

type Search = Promise<{ next?: string; mode?: string }>;

/** The search a sign-in interrupted, for the heading: /search?q=… */
function interruptedQuery(next: string): string | null {
  if (!next.startsWith("/search")) return null;
  const q = new URL(next, "http://x").searchParams.get("q");
  return q?.trim() || null;
}

/** Sign in, or create an account — then on to where the reader was going. */
export default async function SignIn({ searchParams }: { searchParams: Search }) {
  const sp = await searchParams;
  const next = safeNext(sp.next, "/");
  if (await getViewer()) redirect(next);
  return (
    <main className="auth-page">
      <AuthCard next={next} query={interruptedQuery(next)}
                startWith={sp.mode === "signup" ? "signup" : "signin"} />
    </main>
  );
}
