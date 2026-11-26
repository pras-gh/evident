"use client";

import { useEffect, useState } from "react";

/**
 * The search a sign-up interrupted, continued by itself: a short pause to
 * read the welcome, a countdown that says so, and a way to stay.
 */
export function Continue({ href, label, seconds = 4 }: {
  href: string;
  label: string;
  seconds?: number;
}) {
  const [left, setLeft] = useState(seconds);
  const [stayed, setStayed] = useState(false);
  useEffect(() => {
    if (stayed) return;
    if (left <= 0) {
      window.location.assign(href);
      return;
    }
    const t = setTimeout(() => setLeft((n) => n - 1), 1000);
    return () => clearTimeout(t);
  }, [left, stayed, href]);
  return (
    <div className="welcome-go">
      <a className="btn-primary welcome-cta" href={href}>{label}</a>
      {!stayed && left > 0 && (
        <p className="welcome-auto" role="status">
          Continuing in {left}s ·{" "}
          <button type="button" className="auth-link" onClick={() => setStayed(true)}>Stay here</button>
        </p>
      )}
    </div>
  );
}
