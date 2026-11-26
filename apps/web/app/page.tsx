import { AppHeader } from "@/components/brand/AppHeader";
import { CompanyDirectory } from "@/components/home/CompanyDirectory";
import { JustFiled } from "@/components/home/JustFiled";
import { redirect } from "next/navigation";
import { getCompanies, getLatestFilings, getSyncStatus } from "@/lib/api";
import { ago } from "@/lib/format";
import type { SyncStatus } from "@/lib/types";

export const revalidate = 60;

/** How much of the S&P 500 is here, and how current. Nothing, before a sync. */
function Coverage({ sync }: { sync: SyncStatus | null }) {
  if (!sync || (!sync.indexed && !sync.last_poll)) return null;
  const poll = sync.last_poll;
  return (
    <p className="dir-status">
      <span className="dir-status-k">S&amp;P 500</span>
      <span>
        <b>{sync.indexed}</b> of {sync.tracked} companies
      </span>
      <span>{sync.with_financials} with financials</span>
      {sync.loaded > 0 && (
        <span title="Companies whose filings' full text is in Parallx: searchable, citable to the paragraph">
          {sync.loaded} with full filings
        </span>
      )}
      {poll && (
        <span title={new Date(poll.started_at).toUTCString()}>
          {poll.error ? "EDGAR unreachable " : "EDGAR checked "}
          <time dateTime={poll.started_at}>{ago(poll.started_at)}</time>
        </span>
      )}
    </p>
  );
}

/** Every company on record. With exactly one, go straight to it. */
export default async function Home() {
  let companies;
  try {
    companies = await getCompanies();
  } catch {
    return (
      <>
        <AppHeader />
        <main className="tl2-shell">
          <h1 className="ev-title">Parallx</h1>
          <p className="tl2-coverage">
            The API is not reachable. Start it with <code>tools/dev.sh api</code>.
          </p>
        </main>
      </>
    );
  }
  const withTicker = companies.filter((c) => c.ticker);
  if (withTicker.length === 1) redirect(`/memory/${withTicker[0].ticker!.toLowerCase()}`);
  const [sync, latest] = await Promise.all([
    getSyncStatus().catch(() => null),
    getLatestFilings(8).catch(() => []),
  ]);

  return (
    <>
      <AppHeader />
      <main className="tl2-shell">
        <header className="dir-head">
          <h1 className="ev-title">Companies</h1>
          <Coverage sync={sync} />
        </header>
        <JustFiled filings={latest} />
        {withTicker.length === 0 ? (
          <p className="tl2-coverage">
            No companies yet. Start the sync with <code>tools/dev.sh sync</code>, or search for
            one by name.
          </p>
        ) : (
          <CompanyDirectory companies={withTicker} />
        )}
      </main>
    </>
  );
}
