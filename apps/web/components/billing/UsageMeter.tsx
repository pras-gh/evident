import type { Usage } from "@/lib/types";

/** "1 of 2 free companies used", as words and a meter. */
export function usageLine(u: Usage): string {
  if (u.source === "staff") return "Staff — no limits";
  if (u.source === "free") {
    return `${u.free_credits_used} of ${u.free_credits_total} free companies used`;
  }
  return `${u.companies_used} of ${u.companies_limit} companies`;
}

export function UsageMeter({ usage, compact = false }: { usage: Usage; compact?: boolean }) {
  const total = usage.source === "free" ? usage.free_credits_total : usage.companies_limit ?? 0;
  const used = usage.source === "free" ? usage.free_credits_used : usage.companies_used;
  const pct = total ? Math.min(100, Math.round((used / total) * 100)) : 0;
  return (
    <div className={`meter${compact ? " is-compact" : ""}`}>
      <p className="meter-line">{usageLine(usage)}</p>
      {usage.source !== "staff" && (
        <div className="meter-bar" role="meter" aria-valuemin={0} aria-valuemax={total}
             aria-valuenow={used} aria-label={usageLine(usage)}>
          <i style={{ width: `${pct}%` }} className={used >= total ? "is-full" : undefined} />
        </div>
      )}
    </div>
  );
}
