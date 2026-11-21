"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { CompanyFetch } from "@/lib/types";

/**
 * Waits for an on-demand fetch, then shows the results. It polls the fetch,
 * not the search: re-running the search would ask a model to read the query
 * every few seconds while nothing has changed.
 */
export function FetchWatcher({ ids, every = 3000 }: { ids: number[]; every?: number }) {
  const router = useRouter();
  // read through a ref: a new router object must not restart the polling
  const routerRef = useRef(router);
  routerRef.current = router;
  const [seconds, setSeconds] = useState(0);
  const [stage, setStage] = useState("Queued");
  // keyed on the ids' values, not the array, which is new on every render
  const key = ids.join(",");

  useEffect(() => {
    const watched = key.split(",").filter(Boolean).map(Number);
    let stopped = false;
    const tick = setInterval(() => setSeconds((s) => s + 1), 1000);
    const poll = setInterval(async () => {
      const all = await Promise.all(watched.map((id) =>
        fetch(`/api/fetches/${id}`, { cache: "no-store" })
          .then((r) => (r.ok ? (r.json() as Promise<CompanyFetch>) : null))
          .catch(() => null)));
      if (stopped) return;
      if (all.some((f) => f?.status === "running")) setStage("Downloading and indexing");
      // finished, either way: the search can now say so
      if (all.every((f) => f && f.status !== "queued" && f.status !== "running")) {
        stopped = true;
        clearInterval(poll);
        clearInterval(tick);
        routerRef.current.refresh();
      }
    }, every);
    return () => { stopped = true; clearInterval(tick); clearInterval(poll); };
  }, [key, every]);

  return (
    <span className="srch-wait" aria-live="polite">
      {stage}… {seconds}s
    </span>
  );
}
