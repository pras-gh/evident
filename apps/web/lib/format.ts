/** Number formats for the admin dashboard; safe in server and client code. */

/** $1,234 · $12.34 · $0.0071 · $0 — small AI costs keep their meaningful digits. */
export function usd(value: string | number): string {
  const v = Number(value);
  if (!Number.isFinite(v) || v === 0) return "$0";
  const abs = Math.abs(v);
  if (abs >= 100) return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  if (abs >= 1) return `$${v.toFixed(2)}`;
  return `$${v.toPrecision(2).replace(/0+$/, "").replace(/\.$/, "")}`;
}

/** 1,284 · 12.9K · 4.2M */
export function compact(n: number): string {
  if (Math.abs(n) < 10_000) return n.toLocaleString("en-US");
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(fraction > 0 && fraction < 0.01 ? 1 : 0)}%`;
}
