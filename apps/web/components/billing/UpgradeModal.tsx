"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/Modal";
import type { Paywall, Plan } from "@/lib/types";
import { PlanCards } from "./PlanCards";

/**
 * The upgrade, offered where the reader hit the limit — not an error page.
 * What they have researched stays one click away: the dialog lists it, and
 * closing it leaves them where they were.
 */
export function UpgradeModal({ plans, paywall, current, returnTo, defaultOpen = true }: {
  plans: Plan[];
  paywall: Paywall;
  current?: string | null;
  returnTo: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const companyLimit = paywall.reason === "company_limit";
  const researched = paywall.usage?.companies ?? [];
  return (
    <>
      {!open && (
        <button type="button" className="btn-primary upgrade-reopen" onClick={() => setOpen(true)}>
          See plans
        </button>
      )}
      <Modal open={open} onClose={() => setOpen(false)} label="Upgrade Parallx" wide>
        <div className="upgrade">
          <h2 className="upgrade-title">
            {companyLimit ? "Room for more companies" : "Your next research starts with Parallx."}
          </h2>
          <p className="upgrade-sub">
            {companyLimit
              ? `${paywall.message} Move up a plan to research more companies at once.`
              : "You've used your 2 free company research credits. Upgrade to continue " +
                "researching companies and tracking what management delivers."}
          </p>
          <PlanCards plans={plans} current={current} signedIn returnTo={returnTo} />
          {researched.length > 0 && (
            <nav className="upgrade-mine" aria-label="Companies you've researched">
              <span>Your companies, still open:</span>
              {researched.slice(0, 8).map((c) => c.ticker && (
                <a key={c.ticker} href={`/memory/${c.ticker.toLowerCase()}`}>{c.ticker}</a>
              ))}
            </nav>
          )}
        </div>
      </Modal>
    </>
  );
}
