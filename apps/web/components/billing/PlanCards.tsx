"use client";

import { useEffect, useMemo, useState } from "react";
import { COUNTRIES } from "@/components/auth/AuthCard";
import { RequestFailed, startCheckout } from "@/lib/client-api";
import type { Plan } from "@/lib/types";

export const UNSUPPORTED = new Set(["IN"]);
const COUNTRY_KEY = "parallx.billing.country";

export function dollars(cents: number): string {
  const d = cents / 100;
  return `$${Number.isInteger(d) ? d : d.toFixed(2)}`;
}

/** The reader's billing country: remembered, else read from the browser. */
function useCountry(): [string, (c: string) => void] {
  const [country, setCountry] = useState("US");
  useEffect(() => {
    let saved: string | null = null;
    try { saved = localStorage.getItem(COUNTRY_KEY); } catch { /* private mode */ }
    const region = navigator.language?.split("-")[1]?.toUpperCase();
    setCountry(saved || (region && COUNTRIES.some((c) => c.code === region) ? region : "US"));
  }, []);
  return [country, (c: string) => {
    setCountry(c);
    try { localStorage.setItem(COUNTRY_KEY, c); } catch { /* private mode */ }
  }];
}

/**
 * Iix and IIx, side by side (stacked on a phone), with the billing interval.
 * Annual is two months free. A feature the product does not have yet says
 * "Coming soon"; nothing is shown as included before it works.
 */
export function PlanCards({ plans, current, signedIn, returnTo = "/pricing",
                            initialInterval = "month" }: {
  plans: Plan[];
  /** the reader's plan, if signed in */
  current?: string | null;
  signedIn: boolean;
  returnTo?: string;
  initialInterval?: "month" | "year";
}) {
  const [interval, setInterval] = useState<"month" | "year">(initialInterval);
  const [country, setCountry] = useCountry();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const paid = useMemo(() => plans.filter((p) => p.id !== "free"), [plans]);
  const blocked = UNSUPPORTED.has(country);

  async function choose(plan: Plan) {
    setError(null);
    if (!signedIn) {
      const back = `${returnTo}${returnTo.includes("?") ? "&" : "?"}plan=${plan.id}&interval=${interval}`;
      window.location.assign(`/signin?mode=signup&next=${encodeURIComponent(back)}`);
      return;
    }
    setBusy(plan.id);
    try {
      const key = typeof crypto?.randomUUID === "function"
        ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
      const r = await startCheckout(plan.id, interval, country, key);
      window.location.assign(r.mode === "checkout" && r.url ? r.url
                             : `/billing/success?change=${plan.id}`);
    } catch (e) {
      setError(e instanceof RequestFailed ? e.message : "Couldn't start checkout. Try again.");
      setBusy(null);
    }
  }

  return (
    <div className="plans">
      <div className="plans-bar">
        <div className="seg" role="radiogroup" aria-label="Billing interval">
          {(["month", "year"] as const).map((i) => (
            <button key={i} type="button" role="radio" aria-checked={interval === i}
                    className={interval === i ? "is-on" : undefined}
                    onClick={() => setInterval(i)}>
              {i === "month" ? "Monthly" : <>Annual <span className="seg-tag">2 months free</span></>}
            </button>
          ))}
        </div>
        <label className="plans-country">
          <span>Billing country</span>
          <select value={country} onChange={(e) => setCountry(e.target.value)}>
            {COUNTRIES.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
            <option value="XX">Another country</option>
          </select>
        </label>
      </div>

      {blocked && (
        <p className="plans-note" role="note">
          Billing from India will be in rupees, and isn&rsquo;t open yet. We don&rsquo;t charge
          Indian cards in US dollars. Your two free companies are yours meanwhile.
        </p>
      )}

      <div className="plans-grid">
        {paid.map((p) => {
          const price = interval === "month" ? p.monthly_price : p.annual_price;
          const mine = current === p.id;
          return (
            <article key={p.id} className={`plan${p.recommended ? " is-recommended" : ""}`}
                     aria-label={`${p.name}, ${p.audience}`}>
              {p.recommended && <p className="plan-flag">Recommended</p>}
              <header className="plan-head">
                <h3 className="plan-name">{p.name} <span>— {p.audience}</span></h3>
                <p className="plan-price">
                  <b>{dollars(price)}</b><span>/{interval === "month" ? "month" : "year"}</span>
                </p>
                <p className="plan-sub">
                  {interval === "year"
                    ? `${dollars(Math.round(p.annual_price / 12))}/month, billed yearly`
                    : `or ${dollars(p.annual_price)}/year — two months free`}
                </p>
                <p className="plan-desc">{p.description}</p>
              </header>
              <button type="button" className={p.recommended ? "btn-primary plan-cta" : "btn-ghost plan-cta"}
                      disabled={!!busy || mine || (signedIn && (blocked || !p.purchasable))}
                      onClick={() => choose(p)}>
                {mine ? "Your plan" : busy === p.id ? "Opening checkout…" : `Get ${p.name}`}
              </button>
              <ul className="plan-features">
                {p.features.map((f) => (
                  <li key={f.label} className={f.available ? undefined : "is-soon"}>
                    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden>
                      {f.available
                        ? <path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.7"
                                strokeLinecap="round" strokeLinejoin="round" />
                        : <circle cx="8" cy="8" r="2.2" fill="currentColor" />}
                    </svg>
                    <span>{f.label}</span>
                    {!f.available && <em>Coming soon</em>}
                  </li>
                ))}
              </ul>
            </article>
          );
        })}
      </div>
      {signedIn && paid.some((p) => !p.purchasable) && (
        <p className="plans-note">Payments aren&rsquo;t set up on this server yet.</p>
      )}
      <p className="plans-msg" role="status" aria-live="polite">
        {error && <span className="auth-error">{error}</span>}
      </p>
      <p className="plans-fine">
        Secure checkout by Stripe. Prices in USD. Cancel anytime; your plan runs to the end of
        the period you paid for.
      </p>
    </div>
  );
}
