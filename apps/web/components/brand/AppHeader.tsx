import { getUsage, getViewer } from "@/lib/api";
import { ParallxMark } from "./ParallxMark";
import { ParallxWordmark } from "./ParallxWordmark";

type Section = "memory" | "timeline" | "search" | "pricing" | "account";

/**
 * The bar across the top of every page that isn't the memory dashboard (which
 * carries the brand in its sidebar): the logo home, the sections for the
 * company in view, and the reader — signed in (their plan's usage, their
 * account) or not (Pricing, Sign in). Sticky and translucent, aligned to the
 * page's content column, so the brand is always one place and one size.
 */
export async function AppHeader({ ticker, active }: { ticker?: string | null; active?: Section }) {
  const t = ticker?.toLowerCase() ?? null;
  const viewer = await getViewer();
  const usage = viewer ? await getUsage() : null;
  const link = (section: Section, href: string, label: string) => (
    <a href={href} aria-current={active === section ? "page" : undefined}>{label}</a>
  );
  const free = usage?.source === "free";
  return (
    <header className="apphead">
      <div className="apphead-in">
        <a className="apphead-brand" href="/" aria-label="Parallx — home">
          <ParallxMark size={20} />
          <ParallxWordmark height={10} className="apphead-word" />
        </a>
        <nav className="apphead-nav" aria-label="Sections">
          {t && link("memory", `/memory/${t}`, "Memory")}
          {t && link("timeline", `/timeline/${t}`, "Timeline")}
          {link("search", t ? `/search?ticker=${encodeURIComponent(t)}` : "/search", "Search")}
          {link("pricing", "/pricing", "Pricing")}
        </nav>
        <div className="apphead-me">
          {viewer ? (
            <>
              {usage && usage.source !== "staff" && (
                <a className={`pill${free && usage.companies_left === 0 ? " is-out" : ""}`}
                   href={free ? "/pricing" : "/account"}
                   title={free ? "Free company research credits" : "Companies on your plan"}>
                  {free ? `${usage.free_credits_used} of ${usage.free_credits_total} free`
                        : `${usage.plan_name} · ${usage.companies_used}/${usage.companies_limit}`}
                </a>
              )}
              <a className="apphead-avatar" href="/account" aria-label="Your account"
                 aria-current={active === "account" ? "page" : undefined}>
                {(viewer.name || viewer.email || viewer.phone || "?").trim()[0]?.toUpperCase()}
              </a>
            </>
          ) : (
            <a className="btn-ghost apphead-signin" href="/signin">Sign in</a>
          )}
        </div>
      </div>
    </header>
  );
}
