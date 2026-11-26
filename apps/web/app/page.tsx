import { AppHeader } from "@/components/brand/AppHeader";
import { CompanyDirectory } from "@/components/home/CompanyDirectory";
import { JustFiled } from "@/components/home/JustFiled";
import { getCompanies, getLatestFilings, getSubscription, getSyncStatus, getUsage,
         getViewer } from "@/lib/api";
import { SearchBox } from "@/components/auth/SearchBox";
import { UsageMeter } from "@/components/billing/UsageMeter";
import type { Me, Subscription, Usage } from "@/lib/types";
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

/** Signed in: their research, their plan, the search. */
function Dashboard({ me, usage, sub }: { me: Me; usage: Usage | null; sub: Subscription | null }) {
  const first = me.name?.split(" ")[0];
  const recent = usage?.companies.slice(0, 6) ?? [];
  const free = usage?.source === "free";
  return (
    <section className="dash" aria-labelledby="dash-h">
      <h1 id="dash-h" className="dash-hello">{first ? `Welcome back, ${first}` : "Welcome back"}</h1>
      <SearchBox signedIn />
      <div className="dash-cards">
        {usage && (
          <div className="dash-card">
            <p className="dash-k">Usage</p>
            <UsageMeter usage={usage} />
            {free && (
              <a className="dash-link" href="/pricing">
                {usage.companies_left === 0 ? "Upgrade to research more" : "See plans"} →
              </a>
            )}
          </div>
        )}
        <div className="dash-card">
          <p className="dash-k">Plan</p>
          <p className="dash-plan">{sub?.plan_name ?? "Free"}</p>
          <p className="dash-meta">
            {sub?.source === "grace" ? "Payment failed — update your card in your account"
              : sub?.cancel_at_period_end ? "Cancelled, runs to the end of the period"
              : sub?.source === "subscription" ? `Billed ${sub.billing_interval === "year" ? "yearly" : "monthly"}`
              : "Two companies free"}
          </p>
          <a className="dash-link" href={free ? "/pricing" : "/account"}>
            {free ? "Upgrade" : "Manage"} →
          </a>
        </div>
      </div>
      <div className="dash-mine">
        <p className="dash-k">Your companies</p>
        {recent.length ? (
          <ul className="dash-cos">
            {recent.map((c) => c.ticker && (
              <li key={c.ticker}>
                <a href={`/memory/${c.ticker.toLowerCase()}`}>
                  <span className="dir-tk">{c.ticker}</span>
                  <span className="dash-co">{c.name}</span>
                  {c.status === "pending" && <span className="dir-indexing" aria-label="fetching" />}
                </a>
              </li>
            ))}
          </ul>
        ) : (
          <p className="dash-meta">Research your first company: search for it above.</p>
        )}
      </div>
    </section>
  );
}

/** Signed out: what this is, and the search — which signs them in first. */
function Intro() {
  return (
    <section className="intro" aria-labelledby="intro-h">
      <h1 id="intro-h">Understand any public company, with the evidence attached.</h1>
      <p className="intro-sub">Search a company. Every answer cites the filing, page and paragraph.
        Two companies free — no card needed.</p>
      <SearchBox signedIn={false} />
    </section>
  );
}

/** The dashboard (signed in) or the intro, then every company on record. */
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
  const [sync, latest, me] = await Promise.all([
    getSyncStatus().catch(() => null),
    getLatestFilings(8).catch(() => []),
    getViewer(),
  ]);
  const [usage, sub] = me ? await Promise.all([getUsage(), getSubscription()]) : [null, null];

  return (
    <>
      <AppHeader />
      <main className="tl2-shell">
        {me ? <Dashboard me={me} usage={usage} sub={sub} /> : <Intro />}
        <header className="dir-head">
          <h2 className="ev-title">All companies</h2>
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
