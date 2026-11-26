import type { Filing } from "@/lib/types";

/** A company's latest filings from the index, each linked to EDGAR. */
export function FilingsRail({ filings }: { filings: Filing[] }) {
  return (
    <section className="rail-card">
      <h3>Filings</h3>
      {filings.length === 0 ? (
        <p className="tl-sub">No filings indexed yet.</p>
      ) : (
        <ol className="fl">
          {filings.map((f) => (
            <li key={f.accession} className="fl-item">
              <a href={f.url} target="_blank" rel="noreferrer" className="fl-link">
                <span className="fl-form">{f.form_type}</span>
                <span className="fl-body">
                  <span className="fl-date">
                    <time dateTime={f.accepted_at}>{f.filed_date}</time>
                    {f.loaded && <span className="fl-loaded">in Parallx</span>}
                  </span>
                  {f.about && <span className="fl-about">{f.about}</span>}
                </span>
              </a>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
