import type { Filing } from "@/lib/types";
import { ago } from "@/lib/format";

/** The newest filings across the S&P 500: what the sync worker alerts on. */
export function JustFiled({ filings }: { filings: Filing[] }) {
  if (filings.length === 0) return null;
  return (
    <section className="jf" aria-labelledby="jf-h">
      <h2 id="jf-h" className="dir-sector">
        Just filed
      </h2>
      <ol className="jf-list">
        {filings.map((f) => (
          <li key={f.accession}>
            <a className="jf-row" href={f.ticker ? `/memory/${f.ticker.toLowerCase()}` : f.url}>
              <span className="jf-when">
                <time dateTime={f.accepted_at} title={new Date(f.accepted_at).toUTCString()}>
                  {ago(f.accepted_at)}
                </time>
              </span>
              <span className="dir-tk">{f.ticker}</span>
              <span className="jf-form">{f.form_type}</span>
              <span className="jf-about">{f.about ?? f.company}</span>
            </a>
          </li>
        ))}
      </ol>
    </section>
  );
}
