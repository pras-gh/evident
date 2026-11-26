"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/Modal";
import { AuthCard } from "./AuthCard";

/**
 * The company search. Signed in, it searches. Signed out, it keeps what was
 * typed and opens sign-in; the search runs as soon as they're in — nobody
 * types a company twice.
 */
export function SearchBox({ signedIn, defaultValue = "", placeholder, autoFocus = false,
                            className = "dash-search" }: {
  signedIn: boolean;
  defaultValue?: string;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
}) {
  const [q, setQ] = useState(defaultValue);
  const [asking, setAsking] = useState(false);
  const query = q.trim();
  const next = `/search?q=${encodeURIComponent(query)}`;

  function submit(e: React.FormEvent) {
    if (query.length < 2) {
      e.preventDefault();
      return;
    }
    if (!signedIn) {
      e.preventDefault();
      setAsking(true);
    }
  }

  return (
    <>
      <form className={className} action="/search" method="get" role="search" onSubmit={submit}>
        <svg aria-hidden="true" viewBox="0 0 16 16" width="16" height="16">
          <circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M10.5 10.5 14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input name="q" type="search" value={q} onChange={(e) => setQ(e.target.value)}
               placeholder={placeholder ?? "Search a company or ask about one — Nvidia, AAPL, “why is Microsoft spending on AI?”"}
               aria-label="Search companies" autoComplete="off" autoFocus={autoFocus}
               minLength={2} />
        <button type="submit" className="btn-primary">Search</button>
      </form>
      <Modal open={asking} onClose={() => setAsking(false)} label="Sign in to search">
        <AuthCard next={next} query={query} startWith="signup" />
      </Modal>
    </>
  );
}
