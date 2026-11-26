"use client";

import { useState } from "react";
import { cancelSubscription, openPortal, RequestFailed, signOut } from "@/lib/client-api";

/** Manage billing (Stripe's portal), cancel, sign out. */
export function AccountActions({ hasBilling, canCancel, periodEnd }: {
  hasBilling: boolean;
  canCancel: boolean;
  periodEnd: string | null;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function act(label: string, fn: () => Promise<void>) {
    setBusy(label);
    setMsg(null);
    try {
      await fn();
    } catch (e) {
      setMsg(e instanceof RequestFailed ? e.message : "That didn't work. Try again.");
    } finally {
      setBusy(null);
    }
  }
  const until = periodEnd ? new Date(periodEnd).toLocaleDateString(undefined,
    { day: "numeric", month: "long", year: "numeric" }) : "the end of the period";

  return (
    <div className="acct-actions">
      {hasBilling && (
        <button type="button" className="btn-ghost" disabled={!!busy}
                onClick={() => act("portal", async () => {
                  window.location.assign((await openPortal()).url);
                })}>
          {busy === "portal" ? "Opening…" : "Manage billing"}
        </button>
      )}
      {canCancel && !confirming && (
        <button type="button" className="btn-ghost" onClick={() => setConfirming(true)}>
          Cancel subscription
        </button>
      )}
      {confirming && (
        <div className="acct-confirm" role="alertdialog" aria-label="Cancel subscription">
          <p>Your plan stays on until {until}, then doesn&rsquo;t renew. Everything you&rsquo;ve
            researched stays open.</p>
          <button type="button" className="btn-ghost" disabled={!!busy}
                  onClick={() => act("cancel", async () => {
                    await cancelSubscription();
                    setConfirming(false);
                    setMsg(`Cancelled. Your plan runs until ${until}.`);
                  })}>
            {busy === "cancel" ? "Cancelling…" : "Yes, cancel"}
          </button>
          <button type="button" className="auth-link" onClick={() => setConfirming(false)}>
            Keep my plan
          </button>
        </div>
      )}
      <button type="button" className="auth-link acct-signout" disabled={!!busy}
              onClick={() => act("out", async () => {
                await signOut();
                window.location.assign("/");
              })}>
        Sign out
      </button>
      <p role="status" aria-live="polite" className="acct-msg">{msg}</p>
    </div>
  );
}
