"use client";

import { useEffect, useState } from "react";
import { AppHeaderLite } from "@/components/brand/AppHeaderLite";
import { getMySubscription } from "@/lib/client-api";
import type { Subscription } from "@/lib/types";

const EVERY_MS = 2000;
const GIVE_UP_MS = 90_000;

/**
 * After Stripe Checkout. The redirect here proves nothing, so this page
 * waits — showing that it is — until the API, told by Stripe's signed
 * webhook, says the plan is active.
 */
export default function BillingSuccess() {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [state, setState] = useState<"waiting" | "active" | "slow" | "error">("waiting");

  useEffect(() => {
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    async function poll() {
      try {
        const s = await getMySubscription();
        if (stopped) return;
        setSub(s);
        const change = new URLSearchParams(window.location.search).get("change");
        const done = s.source === "subscription" && (!change || s.plan_id === change);
        if (done) return setState("active");
      } catch {
        if (Date.now() - started > 20_000) return setState("error");
      }
      if (Date.now() - started > GIVE_UP_MS) return setState("slow");
      timer = setTimeout(poll, EVERY_MS);
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, []);

  return (
    <>
      <AppHeaderLite />
      <main className="billing-done" aria-live="polite">
        {state === "waiting" && (
          <>
            <span className="spinner" aria-hidden />
            <h1>Confirming your subscription…</h1>
            <p>Stripe is telling us your payment went through. This usually takes a few seconds.</p>
          </>
        )}
        {state === "active" && sub && (
          <>
            <span className="done-tick" aria-hidden>✓</span>
            <h1>You&rsquo;re on {sub.plan_name}.</h1>
            <p>
              Research more companies, with the evidence attached.
              {sub.current_period_end &&
                ` Renews ${new Date(sub.current_period_end).toLocaleDateString(undefined,
                  { day: "numeric", month: "long", year: "numeric" })}.`}
            </p>
            <a className="btn-primary" href="/">Start researching</a>
          </>
        )}
        {state === "slow" && (
          <>
            <h1>Still confirming</h1>
            <p>
              Stripe hasn&rsquo;t confirmed the payment yet. Your plan switches on as soon as it
              does — there&rsquo;s nothing more to do. You can keep researching meanwhile.
            </p>
            <a className="btn-ghost" href="/account">See your account</a>
          </>
        )}
        {state === "error" && (
          <>
            <h1>We couldn&rsquo;t check your subscription</h1>
            <p>If you completed checkout, your plan switches on once Stripe confirms it.</p>
            <a className="btn-ghost" href="/account">See your account</a>
          </>
        )}
      </main>
    </>
  );
}
