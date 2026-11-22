import { ParallxMark } from "@/components/brand/ParallxMark";
import {
  compact,
  percent,
  usd,
  type UsageFilters,
  type UsagePoint,
  type UsageRow,
  type UsageSummary,
} from "@/lib/admin";
import { UsageColumns } from "./UsageColumns";

const PRESETS = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];

function isoDaysAgo(today: string, days: number): string {
  const d = new Date(`${today}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - (days - 1));
  return d.toISOString().slice(0, 10);
}

/** Query string for a filter change, keeping the other filters. */
export function withFilters(f: UsageFilters, change: Partial<UsageFilters>): string {
  const next = { ...f, ...change };
  const q = new URLSearchParams(
    Object.entries(next).filter(([, v]) => v) as [string, string][],
  );
  return q.size ? `?${q}` : "?";
}

/** "2026-11-20 19:27" in UTC, whatever offset the timestamp arrived with. */
export function utcMinute(timestamp: string): string {
  return new Date(timestamp).toISOString().slice(0, 16).replace("T", " ");
}

function dayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short", day: "numeric", timeZone: "UTC",
  });
}

function monthLabel(ym: string): string {
  return new Date(`${ym}-01T00:00:00Z`).toLocaleDateString("en-US", {
    month: "short", year: "numeric", timeZone: "UTC",
  });
}

/** Every day in the range, with zero where nothing was spent — a missing day
 *  is a day of no spend, and the chart should say so rather than close the gap. */
export function fillDays(points: UsagePoint[], from: string, to: string): UsagePoint[] {
  const by = new Map(points.map((p) => [p.period, p]));
  const out: UsagePoint[] = [];
  for (let d = new Date(`${from}T00:00:00Z`); d <= new Date(`${to}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + 1)) {
    const key = d.toISOString().slice(0, 10);
    out.push(by.get(key) ?? { period: key, cost_usd: "0", requests: 0 });
  }
  return out;
}

/**
 * The internal AI usage dashboard. Server-rendered from one summary response:
 * every number on the page comes from the same filtered slice, so they agree.
 */
export function UsageDashboard({
  data,
  filters,
  today,
}: {
  data: UsageSummary;
  filters: UsageFilters;
  today: string;
}) {
  const t = data.totals;
  const days = fillDays(data.daily, data.date_from, data.date_to);
  const customer = data.facets.organizations.find((o) => o.id === filters.organization_id);

  return (
    <main className="adm">
      <header className="adm-head">
        <div>
          <p className="ev-kicker adm-kicker">
            <ParallxMark size={14} />
            Parallx · Internal
          </p>
          <h1 className="ev-title">AI usage</h1>
          <p className="adm-sub">
            {dayLabel(data.date_from)} – {dayLabel(data.date_to)}, UTC
            {customer ? ` · ${customer.name}` : " · all customers"}
            {filters.provider ? ` · ${filters.provider}` : ""}
            {filters.model ? ` · ${filters.model}` : ""}
          </p>
        </div>
        <form method="post" action="/admin/session?logout">
          <button type="submit" className="adm-btn is-ghost">Sign out</button>
        </form>
      </header>

      {/* one row of filters, above everything they scope */}
      <form method="get" className="adm-filters" aria-label="Filters">
        <nav className="adm-presets" aria-label="Date range">
          {PRESETS.map((p) => {
            const from = isoDaysAgo(today, p.days);
            const on = data.date_from === from && data.date_to === today;
            return (
              <a key={p.days} href={withFilters(filters, { date_from: from, date_to: today })}
                className="adm-chip" aria-current={on ? "true" : undefined}>
                {p.label}
              </a>
            );
          })}
        </nav>
        <label className="adm-field">
          <span>From</span>
          <input type="date" name="date_from" defaultValue={data.date_from} max={today} />
        </label>
        <label className="adm-field">
          <span>To</span>
          <input type="date" name="date_to" defaultValue={data.date_to} max={today} />
        </label>
        <label className="adm-field">
          <span>Provider</span>
          <select name="provider" defaultValue={filters.provider ?? ""}>
            <option value="">All</option>
            {data.facets.providers.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="adm-field">
          <span>Model</span>
          <select name="model" defaultValue={filters.model ?? ""}>
            <option value="">All</option>
            {data.facets.models.map((m) => {
              const model = m.slice(m.indexOf(":") + 1);
              return <option key={m} value={model}>{m}</option>;
            })}
          </select>
        </label>
        <label className="adm-field">
          <span>Customer</span>
          <select name="organization_id" defaultValue={filters.organization_id ?? ""}>
            <option value="">All</option>
            {data.facets.organizations.map((o) => (
              <option key={o.id} value={o.id}>{o.name}</option>
            ))}
          </select>
        </label>
        <button type="submit" className="adm-btn">Apply</button>
        <a href="?" className="adm-reset">Reset</a>
      </form>

      <section className="adm-kpis" aria-label="Totals">
        <div className="adm-hero">
          <span className="adm-kpi-label">Total AI spend</span>
          <span className="adm-hero-value">{usd(t.cost_usd)}</span>
          <span className="adm-kpi-note">{compact(t.requests)} requests</span>
        </div>
        <Kpi label="Requests" value={compact(t.requests)}
          note={`${compact(t.attempts)} provider calls`} />
        <Kpi label="Average cost" value={usd(t.avg_cost_per_request)}
          note="per answered request" />
        <Kpi label="Input tokens" value={compact(t.input_tokens)}
          note={t.cached_tokens ? `${compact(t.cached_tokens)} from prompt cache` : "none cached"} />
        <Kpi label="Output tokens" value={compact(t.output_tokens)} />
        <Kpi label="Failed requests" value={compact(t.failed_requests)}
          note={`${compact(t.failed_attempts)} failed calls`} flag={t.failed_requests > 0} />
        <Kpi label="Fallback rate" value={percent(t.fallback_rate)}
          note={`${compact(t.fallback_requests)} answered by a fallback`}
          flag={t.fallback_rate > 0.1} />
        <Kpi label="Cache hits" value={compact(t.cache_hits)} note="answered at no cost" />
        <Kpi label="Refused by a limit" value={compact(t.blocked_requests)}
          flag={t.blocked_requests > 0} />
      </section>

      <div className="adm-grid">
        <Card title="Daily spend" wide>
          <UsageColumns label="Daily spend"
            columns={days.map((p) => ({ period: p.period, label: dayLabel(p.period),
              value: Number(p.cost_usd), requests: p.requests }))} />
          <TableView rows={days.map((p) => [dayLabel(p.period), usd(p.cost_usd),
            p.requests.toLocaleString("en-US")])} head={["Day", "Spend", "Requests"]} />
        </Card>

        <Card title="Monthly spend">
          <UsageColumns label="Monthly spend"
            columns={data.monthly.map((p) => ({ period: p.period, label: monthLabel(p.period),
              value: Number(p.cost_usd), requests: p.requests }))} />
          <TableView rows={data.monthly.map((p) => [monthLabel(p.period), usd(p.cost_usd),
            p.requests.toLocaleString("en-US")])} head={["Month", "Spend", "Requests"]} />
        </Card>

        <Card title="Cost by customer">
          <BarList rows={data.by_organization}
            link={(r) => withFilters(filters, { organization_id: r.key })} />
        </Card>
        <Card title="Cost by provider">
          <BarList rows={data.by_provider} link={(r) => withFilters(filters, { provider: r.key })} />
        </Card>
        <Card title="Cost by model">
          <BarList rows={data.by_model}
            link={(r) => withFilters(filters, { model: r.key.slice(r.key.indexOf(":") + 1) })} />
        </Card>
        <Card title="Cost by task">
          <BarList rows={data.by_task} />
        </Card>

        <Card title="Failures, fallbacks and refusals" wide>
          {data.recent_failures.length === 0 ? (
            <p className="adm-empty">None in this range.</p>
          ) : (
            <div className="adm-table-wrap">
              <table className="adm-table">
                <thead>
                  <tr><th>When (UTC)</th><th>Outcome</th><th>Customer</th><th>Model</th>
                    <th>Task</th><th>Detail</th></tr>
                </thead>
                <tbody>
                  {data.recent_failures.map((e, i) => (
                    <tr key={`${e.request_id}-${i}`}>
                      <td className="num">{utcMinute(e.created_at)}</td>
                      <td><Outcome status={e.status} /></td>
                      <td>{e.organization}</td>
                      <td>{e.provider}:{e.model}</td>
                      <td>{e.task_type.replace(/_/g, " ")}</td>
                      <td className="adm-detail" title={`request ${e.request_id}`}>
                        {e.error_message ?? "answered by this fallback"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </main>
  );
}

function Kpi({ label, value, note, flag }: {
  label: string; value: string; note?: string; flag?: boolean;
}) {
  return (
    <div className={`adm-kpi${flag ? " is-flagged" : ""}`}>
      <span className="adm-kpi-label">{label}</span>
      <span className="adm-kpi-value">{value}</span>
      {note && <span className="adm-kpi-note">{note}</span>}
    </div>
  );
}

function Card({ title, wide, children }: { title: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <section className={`adm-card${wide ? " is-wide" : ""}`} aria-label={title}>
      <h2 className="adm-card-title">{title}</h2>
      {children}
    </section>
  );
}

/** Ranked bars in one hue: the label names the row, the value sits at the
 *  bar's tip, and the row links to that filter. */
export function BarList({ rows, link }: { rows: UsageRow[]; link?: (r: UsageRow) => string }) {
  if (rows.length === 0) return <p className="adm-empty">Nothing in this range.</p>;
  const max = Math.max(...rows.map((r) => Number(r.cost_usd)), 0) || 1;
  return (
    <ol className="bars">
      {rows.map((r) => {
        const body = (
          <>
            <span className="bars-label" title={r.label}>{r.label}</span>
            <span className="bars-track" aria-hidden>
              <span className="bars-bar" style={{ width: `${(Number(r.cost_usd) / max) * 100}%` }} />
            </span>
            <span className="bars-value">{usd(r.cost_usd)}</span>
            <span className="bars-meta">
              {compact(r.requests)} req{r.failed_attempts ? ` · ${compact(r.failed_attempts)} failed` : ""}
            </span>
          </>
        );
        return (
          <li key={r.key}>
            {link ? <a href={link(r)} className="bars-row">{body}</a>
              : <div className="bars-row">{body}</div>}
          </li>
        );
      })}
    </ol>
  );
}

/** State is never color alone: each outcome has its word and a glyph. */
function Outcome({ status }: { status: "failed" | "fallback" | "blocked" }) {
  const map = {
    failed: ["✕", "Failed call"],
    fallback: ["↪", "Fallback answered"],
    blocked: ["⏸", "Refused by limit"],
  } as const;
  const [glyph, text] = map[status];
  return (
    <span className={`adm-outcome is-${status}`}>
      <span aria-hidden>{glyph}</span> {text}
    </span>
  );
}

function TableView({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <details className="adm-tableview">
      <summary>Table view</summary>
      <table className="adm-table">
        <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j ? "num" : ""}>{c}</td>)}</tr>)}
        </tbody>
      </table>
    </details>
  );
}
