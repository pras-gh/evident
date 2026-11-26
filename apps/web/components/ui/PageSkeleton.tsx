import { AppHeaderLite } from "@/components/brand/AppHeaderLite";

/**
 * What a page looks like before its data arrives: the header in place, and
 * the shape of what is coming, so the page settles instead of appearing.
 */
export function PageSkeleton({ label, variant }: {
  /** said to screen readers while loading */
  label: string;
  variant: "search" | "company" | "account";
}) {
  const rows = variant === "account" ? 3 : 5;
  return (
    <>
      <AppHeaderLite />
      <main className={`skel skel-${variant}`} aria-busy="true">
        <span className="sr-only" role="status">{label}</span>
        {variant === "search" && <div className="skel-bar skel-input" />}
        {variant === "company" && (
          <div className="skel-head">
            <div className="skel-bar skel-logo" />
            <div className="skel-stack">
              <div className="skel-bar" style={{ width: "38%" }} />
              <div className="skel-bar skel-thin" style={{ width: "22%" }} />
            </div>
          </div>
        )}
        {variant === "account" && <div className="skel-bar" style={{ width: "30%" }} />}
        <div className="skel-cards">
          {Array.from({ length: rows }, (_, i) => (
            <div key={i} className="skel-card" style={{ animationDelay: `${i * 70}ms` }}>
              <div className="skel-bar" style={{ width: `${62 - i * 6}%` }} />
              <div className="skel-bar skel-thin" />
              <div className="skel-bar skel-thin" style={{ width: "84%" }} />
            </div>
          ))}
        </div>
      </main>
    </>
  );
}
