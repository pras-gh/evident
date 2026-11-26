import type { Financials } from "@/lib/types";
import { formatValue } from "@/lib/format";

/**
 * A company's statements from its XBRL, a column per fiscal year or quarter,
 * newest first as a 10-K prints them — on a phone, the period that matters
 * is the one in view.
 * Every figure links to the filing that reported it: XBRL tags numbers, not
 * sentences, so the citation is the filing and its concept, shown on hover.
 */
export function FinancialsPanel({ ticker, data }: { ticker: string; data: Financials | null }) {
  const t = ticker.toLowerCase();
  const period = data?.period ?? "annual";
  const derived = data?.rows.some((r) => r.cells.some((c) => c?.derived)) ?? false;
  // the API is chronological; a statement reads newest first
  const columns = [...(data?.columns ?? [])].reverse();
  const rows = (data?.rows ?? []).map((r) => ({ ...r, cells: [...r.cells].reverse() }));

  return (
    <section className="panel fin" aria-labelledby="fin-h">
      <div className="panel-head">
        <div>
          <h2 id="fin-h">Financial statements</h2>
          <p className="panel-sub">From the company&rsquo;s XBRL filings with the SEC.</p>
        </div>
        <nav className="fin-toggle" aria-label="Period">
          {(["annual", "quarterly"] as const).map((p) => (
            <a
              key={p}
              href={p === "annual" ? `/memory/${t}` : `/memory/${t}?period=quarterly`}
              aria-current={p === period ? "page" : undefined}
            >
              {p === "annual" ? "Annual" : "Quarterly"}
            </a>
          ))}
        </nav>
      </div>

      {!data || data.columns.length === 0 ? (
        <p className="fin-empty">
          {data?.loaded_at
            ? "This company has not filed these lines in XBRL."
            : "Statements load with the company’s filing list — not yet for this one."}
        </p>
      ) : (
        <div className="fin-scroll">
          <table className="fin-table">
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Line</span>
                </th>
                {columns.map((c) => (
                  <th scope="col" key={c.period_end} title={`Period ended ${c.period_end}`}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row">{r.label}</th>
                  {r.cells.map((c, i) => (
                    <td key={columns[i].period_end}>
                      {c ? (
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noreferrer"
                          className={c.derived ? "is-derived" : undefined}
                          title={`${c.concept} · ${c.form} filed ${c.filed}${
                            c.derived ? " · derived: the year less its first three quarters" : ""
                          }`}
                        >
                          {formatValue(c.value, r.unit)}
                          {c.derived && <sup aria-hidden>†</sup>}
                        </a>
                      ) : (
                        <span className="fin-none" aria-label="not filed">
                          —
                        </span>
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {derived && (
        <p className="fin-note">
          † Fiscal Q4 is not filed on its own: it is the year less its first three quarters.
        </p>
      )}
    </section>
  );
}
