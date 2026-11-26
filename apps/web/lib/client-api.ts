/**
 * The API from the browser: through the web app's own origin, which proxies
 * `/api/*` to the API (next.config.mjs). Same origin, so the session cookie
 * travels with each call and the API's origin check passes; nothing here
 * holds a secret.
 */
import type { CardDetail, CheckoutResult, Me, Subscription } from "./types";

/** An API path (`/v1/…`) as the browser should fetch it. */
export function asset(apiPath: string): string {
  return apiPath.replace(/^\/v1\//, "/api/");
}

/** A refusal the page can act on: `error` is the API's code. */
export class RequestFailed extends Error {
  constructor(public status: number, public error: string, message: string,
              public body: Record<string, unknown> | null = null) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    cache: "no-store", credentials: "same-origin", ...init,
    headers: { ...(init.body ? { "content-type": "application/json" } : {}),
               ...(init.headers ?? {}) },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new RequestFailed(res.status, body?.error ?? "failed",
                            body?.message ?? "Something went wrong. Try again.", body);
  }
  return body as T;
}

export const getCard = (t: string, kind: string) =>
  call<CardDetail>(`/companies/${t}/cards/${kind}`);

/** Trade a verified identity token for a Parallx session (an httpOnly cookie). */
export const createSession = (idToken: string) =>
  call<{ user: Me; is_new: boolean }>("/auth/session", {
    method: "POST", body: JSON.stringify({ id_token: idToken }) });

export const signOut = () => call<void>("/auth/logout", { method: "POST" });

export const getMySubscription = () => call<Subscription>("/me/subscription");

export const startCheckout = (plan_id: string, interval: "month" | "year", country: string,
                              idempotency_key: string) =>
  call<CheckoutResult>("/billing/checkout", {
    method: "POST", body: JSON.stringify({ plan_id, interval, country, idempotency_key }) });

export const openPortal = () => call<{ url: string }>("/billing/portal");

export const cancelSubscription = () =>
  call<{ status: string; current_period_end: string | null }>("/billing/cancel",
                                                              { method: "POST" });
