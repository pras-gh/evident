import { redirect } from "next/navigation";
import { getCompanies } from "@/lib/api";

export const revalidate = 60;

/** Every company with memory. With exactly one, go straight to it. */
export default async function Home() {
  let companies;
  try {
    companies = await getCompanies();
  } catch {
    return (
      <main className="tl2-shell">
        <h1 className="ev-title">Evident</h1>
        <p className="tl2-coverage">
          The API is not reachable. Start it with <code>tools/dev.sh api</code>.
        </p>
      </main>
    );
  }
  const withTicker = companies.filter((c) => c.ticker);
  if (withTicker.length === 1) redirect(`/memory/${withTicker[0].ticker!.toLowerCase()}`);

  return (
    <main className="tl2-shell">
      <p className="ev-kicker">Evident</p>
      <h1 className="ev-title">Companies</h1>
      {withTicker.length === 0 ? (
        <p className="tl2-coverage">
          No companies yet. Load the demo with <code>tools/db.sh seed</code>, or ingest one
          with <code>POST /v1/ingest</code>.
        </p>
      ) : (
        <ul className="tl2-filters" aria-label="Companies">
          {withTicker.map((c) => (
            <li key={c.cik}>
              <a className="tl2-filter" href={`/memory/${c.ticker!.toLowerCase()}`}>
                {c.ticker} · {c.name}{" "}
                <span className="tl2-count">{c.document_count} filings</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
