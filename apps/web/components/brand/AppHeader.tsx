import { ParallxMark } from "./ParallxMark";
import { ParallxWordmark } from "./ParallxWordmark";

type Section = "memory" | "timeline" | "search";

/**
 * The bar across the top of every page that isn't the memory dashboard (which
 * carries the brand in its sidebar): the logo home, and the sections for the
 * company in view. Sticky and translucent, aligned to the page's content
 * column, so the brand is always one place and one size.
 */
export function AppHeader({ ticker, active }: { ticker?: string | null; active?: Section }) {
  const t = ticker?.toLowerCase() ?? null;
  const link = (section: Section, href: string, label: string) => (
    <a href={href} aria-current={active === section ? "page" : undefined}>{label}</a>
  );
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
        </nav>
      </div>
    </header>
  );
}
