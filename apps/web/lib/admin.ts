/** The admin dashboard's data: GET /v1/ai-usage/summary, fetched on the server
 *  with the admin's key from an httpOnly cookie. Never imported by client code,
 *  so the key never reaches the browser. */

export const ADMIN_COOKIE = "evident_admin";

const BASE = process.env.API_URL ?? "http://localhost:8000/v1";

export interface UsageTotals {
  cost_usd: string;
  requests: number;
  attempts: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  avg_cost_per_request: string;
  failed_requests: number;
  failed_attempts: number;
  fallback_requests: number;
  fallback_rate: number;
  cache_hits: number;
  blocked_requests: number;
}

export interface UsageRow {
  key: string;
  label: string;
  cost_usd: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  failed_attempts: number;
}

export interface UsagePoint {
  period: string;
  cost_usd: string;
  requests: number;
}

export interface UsageEvent {
  created_at: string;
  request_id: string;
  organization: string;
  provider: string;
  model: string;
  task_type: string;
  status: "failed" | "fallback" | "blocked";
  error_message: string | null;
}

export interface UsageSummary {
  organization_id: string | null;
  user_id: string | null;
  date_from: string;
  date_to: string;
  totals: UsageTotals;
  by_provider: UsageRow[];
  by_model: UsageRow[];
  by_organization: UsageRow[];
  by_task: UsageRow[];
  daily: UsagePoint[];
  monthly: UsagePoint[];
  recent_failures: UsageEvent[];
  facets: {
    providers: string[];
    models: string[];
    organizations: { id: string; slug: string; name: string; is_internal: boolean }[];
  };
}

export interface Me {
  user_id: string;
  email: string;
  role: string;
  organization: { id: string; slug: string; name: string; is_internal: boolean };
}

export class AdminApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function adminGet<T>(path: string, key: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Authorization: `Bearer ${key}` },
    cache: "no-store",
  });
  if (!res.ok) throw new AdminApiError(res.status, `${res.status} — ${path}`);
  return res.json() as Promise<T>;
}

/** The filters the dashboard accepts, from the page's query string. */
export interface UsageFilters {
  date_from?: string;
  date_to?: string;
  provider?: string;
  model?: string;
  organization_id?: string;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const UUID = /^[0-9a-f-]{36}$/i;

export function parseFilters(sp: Record<string, string | string[] | undefined>): UsageFilters {
  const one = (k: string) => {
    const v = sp[k];
    return (Array.isArray(v) ? v[0] : v)?.trim() || undefined;
  };
  const out: UsageFilters = {};
  const from = one("date_from");
  const to = one("date_to");
  if (from && DATE.test(from)) out.date_from = from;
  if (to && DATE.test(to)) out.date_to = to;
  if (one("provider")) out.provider = one("provider");
  if (one("model")) out.model = one("model");
  const org = one("organization_id");
  if (org && UUID.test(org)) out.organization_id = org;
  return out;
}

export function summaryPath(f: UsageFilters): string {
  const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]);
  return `/ai-usage/summary${q.size ? `?${q}` : ""}`;
}

export { compact, percent, usd } from "./format";
