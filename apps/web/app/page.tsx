import { AppHeader } from "@/components/brand/AppHeader";
import { CompanyDirectory } from "@/components/home/CompanyDirectory";
import { redirect } from "next/navigation";
import { getCompanies, getSyncStatus } from "@/lib/api";
import type { SyncStatus } from "@/lib/types";

export const revalidate = 60;

function ago(iso: string, now = Date.now()): string {
  const min = Math.round((now - Date.parse(iso)) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

/** How much of the S&P 500 is here, and how current. Nothing, before a sync. */
function Coverage({ sync }: { sync: SyncStatus | null }) {
  if (!sync || (!sync.with_annual_report && !sync.last_poll)) return null;
  const poll = sync.last_poll;
  return (
    <p className="dir-status">
      <span className="dir-status-k">S&amp;P 500</span>
      <span>
        <b>{sync.searchable}</b> of {sync.tracked} searchable
      </span>
      {sync.with_annual_report > sync.searchable && (
        <span>{sync.with_annual_report - sync.searchable} indexing</span>
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
  const sync = await getSyncStatus().catch(() => null);

  return (
    <>
      <AppHeader />
      <main className="tl2-shell">
        <header className="dir-head">
          <h1 className="ev-title">Companies</h1>
          <Coverage sync={sync} />
        </header>
        {withTicker.length === 0 ? (
          <p className="tl2-coverage">
            No companies yet. Start the sync with{" "}
            <code>python -m workers.sync_worker watch</code>, or search for one by name.
          </p>
        ) : (
          <CompanyDirectory companies={withTicker} />
        )}
      </main>
    </>
  );
}
