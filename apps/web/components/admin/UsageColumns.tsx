"use client";

import { useState } from "react";
import { usd } from "@/lib/format";

export interface Column {
  period: string;
  label: string;
  value: number;
  requests: number;
}

/** Clean axis steps: 1, 2 or 5 × a power of ten, about four of them. */
export function ticks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const top = Math.ceil(max / step) * step;
  return Array.from({ length: Math.round(top / step) + 1 }, (_, i) => +(i * step).toPrecision(12));
}

/**
 * Spend per period as columns: one series, so no legend — the card's title
 * names it. Each column is its own hover and focus target, taller than the
 * paint; the tooltip leads with the value. Every value is also in the table
 * view beside it, so the tooltip never gates anything.
 */
export function UsageColumns({
  columns,
  label,
}: {
  columns: Column[];
  label: string;
}) {
  // a server component renders this; functions cannot cross that boundary
  const format = usd;
  const [active, setActive] = useState<number | null>(null);
  const axis = ticks(Math.max(0, ...columns.map((c) => c.value)));
  const top = axis[axis.length - 1] || 1;

  if (columns.length === 0) return <p className="adm-empty">No spend in this range.</p>;

  const shown = active != null ? columns[active] : null;
  return (
    <div className="cols" role="group" aria-label={label}>
      <div className="cols-axis" aria-hidden>
        {axis.map((t) => (
          <span key={t} className="cols-tick" style={{ bottom: `${(t / top) * 100}%` }}>
            <span className="cols-tick-label">{format(t)}</span>
          </span>
        ))}
      </div>
      <div className="cols-plot" onPointerLeave={() => setActive(null)}>
        {columns.map((c, i) => (
          <button
            key={c.period}
            type="button"
            className={`col${active === i ? " is-active" : ""}`}
            aria-label={`${c.label}: ${format(c.value)}, ${c.requests} requests`}
            onPointerEnter={() => setActive(i)}
            onFocus={() => setActive(i)}
            onBlur={() => setActive(null)}
          >
            <span className="col-bar" style={{ height: `${(c.value / top) * 100}%` }} />
          </button>
        ))}
        {shown && (
          <div className="cols-tip" role="status"
            style={{ left: `${((active! + 0.5) / columns.length) * 100}%` }}>
            <strong>{format(shown.value)}</strong>
            <span>{shown.label}</span>
            <span>{shown.requests.toLocaleString("en-US")} requests</span>
          </div>
        )}
      </div>
      <div className="cols-x" aria-hidden>
        <span>{columns[0].label}</span>
        {columns.length > 1 && <span>{columns[columns.length - 1].label}</span>}
      </div>
    </div>
  );
}
