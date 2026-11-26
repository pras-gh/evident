/** Number and time formats; safe in server and client code. */

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

// ------------------------------------------------- financial statements

const MINUS = "\u2212"; // a true minus sign, not a hyphen

function scaled(abs: number): string {
  if (abs >= 1e12) return `${(abs / 1e12).toFixed(abs >= 1e13 ? 1 : 2)}T`;
  if (abs >= 1e9) return `${(abs / 1e9).toFixed(abs >= 1e11 ? 0 : 1)}B`;
  if (abs >= 1e6) return `${(abs / 1e6).toFixed(abs >= 1e8 ? 0 : 1)}M`;
  if (abs >= 1e3) return `${(abs / 1e3).toFixed(0)}K`;
  return abs.toFixed(0);
}

/** $130B, $2.90 a share, 24.4B shares; a true minus sign for losses. */
export function formatValue(value: number, unit: string): string {
  const sign = value < 0 ? MINUS : "";
  const abs = Math.abs(value);
  if (unit === "USD/shares") return `${sign}$${abs.toFixed(2)}`;
  if (unit === "USD") return `${sign}$${scaled(abs)}`;
  return `${sign}${scaled(abs)}`;
}

/** "4 min ago", "3 h ago", "2 days ago". */
export function ago(iso: string, now = Date.now()): string {
  const min = Math.round((now - Date.parse(iso)) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}
