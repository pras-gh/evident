# S&P 500 sync

Parallx tracks the S&P 500 from EDGAR's own interfaces. Each source does one
job:

| Need | Source | How |
| --- | --- | --- |
| Latest filings | Submissions API | Every tracked company's filing list (`filings`): 10-K, 10-Q, 8-K, metadata only |
| Notifications | Latest-filings feed (RSS), polled | Every 10 minutes; a new filing alerts (log, and a webhook if set) |
| Financial statements | Company Facts API | Statement lines, one value per period (`financial_facts`) |
| Full filing content | EDGAR Archives (HTML) | Downloaded on demand, then kept current |
| History, backtesting | Nightly bulk ZIPs | `tools/sec_bulk.py`: whole filing lists and every statement |

```bash
tools/dev.sh sync                              # the long-running worker
python -m workers.sync_worker status           # coverage, last checks, latest filings
python -m workers.sync_worker backfill         # filing lists and statements, all 500
python -m workers.sync_worker backfill --documents 20   # ...and the top 20's annual reports
python -m workers.sync_worker poll             # one look at what was just filed
python -m workers.sync_worker sweep --since 2026-11-20T00:00:00+00:00
python tools/sec_bulk.py download && python tools/sec_bulk.py load
python tools/sp500.py                          # refresh the list when the index changes
```

Everything that reaches EDGAR needs `SEC_USER_AGENT` in `.env`, with a real
contact, such as `Parallx ingest (you@example.com)`. EDGAR refuses automated
traffic without one.

## Which companies

The list is `config/sp500.csv`: 503 tickers and 500 companies, taken from
Wikipedia's constituents table, which carries each company's SEC CIK.
Everything keys on the CIK. Tickers change, and share classes repeat:
Alphabet is listed as both GOOGL and GOOG but files once, under one CIK. When
the index changes, run `tools/sp500.py`. The worker reads the list on every
turn and indexes whoever joined.

## Text on demand

A filing's text, the HTML in EDGAR's Archives, is downloaded when someone asks
about the company. A search that names it fetches its latest annual report,
then parses and embeds it. Every answer is then cited to a paragraph.

Downloading and embedding all 500 annual reports up front would take days of
embedding quota, for companies nobody has asked about.

Once a company's filings are loaded, its new 10-Ks and 10-Qs are downloaded as
soon as they are filed. So whatever people use stays current. A download that
fails is tried again at the next poll, three times in all.

For a demo that shouldn't wait on a search, `backfill --documents N` preloads
the latest annual report of the N most-asked-about companies.

## What the worker does

`workers/sync_worker.py watch` repeats three jobs. On each turn it does the
first one that has work:

1. **Poll.** Every 10 minutes it reads the latest-filings feed for 10-K, 10-Q
   and 8-K. That is one request per form, covering every company. For each
   tracked company with a filing not indexed yet, it:
   - reads the company's filing list from the Submissions API;
   - reloads its statements from Company Facts, if a 10-K or 10-Q is new;
   - downloads the new filing's text, if the company's filings are loaded;
   - sends an alert.
2. **Embed.** Chunks without a vector are embedded, newest filing first. A
   rate limit or spent quota pauses this step, with the wait growing from 2
   minutes up to an hour.
3. **Backfill.** A tracked company that has never been indexed gets its filing
   list and its statements: two requests, one company per turn. All 500 take
   minutes.

**Catching up.** If the worker was away for more than two hours (a laptop
asleep, a deploy), the feed may not reach back far enough. So it starts with a
sweep: it re-reads every company's filing list and alerts on what was accepted
since the last good check.

**What alerts.** A new filing found by a poll or a sweep, accepted within the
last two days. A company's first indexing adds its whole recent history,
which is not news. So the only alerts then are for what the feed just listed.

## Alerts

Every alert is logged, for example `NVDA filed an 8-K (current report):
results of operations`. With `ALERT_WEBHOOK_URL` set, each alert is also
POSTed as JSON:

- `text`: one line, which Slack's incoming webhooks display;
- `filing`: the filing's fields, for a receiver of your own.

`ALERT_FORMS` limits which forms alert (default
`10-K,10-Q,8-K,20-F,40-F,6-K`). Delivery is best effort: a webhook that is down
never stops a sync. A filing whose alert wasn't delivered keeps `alerted_at`
null.

## Financial statements

The Company Facts API returns every value a company ever tagged, as filed. A
quarter's revenue appears in the 10-Q that reported it, again in next year's
10-Q, and again in the 10-K. For each calendar period, EDGAR picks one value
(the one filed last) and labels it with a `frame` (`CY2025`, `CY2025Q3`,
`CY2025Q3I`).

Parallx keeps only framed values. That gives one value per period,
restatements included. It keeps them only from periodic reports and only for
the lines in `parallx_parser/facts.py`:
- Revenue, cost of revenue, gross profit, R&D, operating income and net
  income.
- Diluted EPS.
- Operating cash flow and capital expenditure.
- Cash, assets, liabilities, equity and long-term debt.
- Shares outstanding.

A line can be tagged several ways, so each line lists its XBRL concepts,
preferred first. Revenue, for example, moved from `SalesRevenueNet` to
`RevenueFromContractWithCustomer…` in 2018.

`GET /v1/companies/{ticker}/financials?period=annual|quarterly` lays the values
out as statements:

- **Columns:** one per fiscal year or quarter, named as the company names them
  (FY2025, Q3 FY2025).
- **Fiscal Q4:** no filing reports it on its own. Where a line adds up, Q4 is
  the year less its first three quarters, and is marked as derived. EPS and
  share counts don't add up, so they get no derived Q4.
- **Citations:** every value links to the filing that reported it, with its
  XBRL concept. XBRL tags a number, not a sentence, so there is no paragraph
  to cite.

## Where it is recorded

- **`filings`:** the index, one row per filing:
  - `accepted_at`: when EDGAR accepted it;
  - `first_seen_at`: when Parallx first knew of it;
  - `alerted_at`: when its alert went out; null for history.
- **`financial_facts`:** statements, unique per company, concept, unit and
  frame.
- **`companies.filings_indexed_at` / `facts_loaded_at`:** what the backfill has
  done.
- **`company_fetches`:** one row per download, started by a search
  (`source = search`) or by the worker (`backfill`, `watch`). Only one can be
  active per company.
- **`sync_runs`:** each poll and sweep.

API:

- `GET /v1/sync` — how many companies are indexed, have financials, are
  loaded and are searchable; the last checks; the newest filings.
- `GET /v1/filings/latest?days=14` — what was just filed, across companies.
- `GET /v1/companies/{ticker}/filings` — one company's filings.

## History and backtesting

The API path keeps the roughly 1,000 most recent filings per company.
`tools/sec_bulk.py` loads the rest: older submission pages and every statement
a company has filed. It reads them from EDGAR's nightly `submissions.zip` and
`companyfacts.zip`, each over a gigabyte, through the same code as the API
path. It reads only the tracked companies' entries and doesn't unpack the
rest.

## Search, with 500 companies on record

- **On record means searchable:** a company counts once some of its chunks are
  embedded. Naming a company that is indexed but not loaded fetches it,
  instead of searching it and finding nothing.
- **Ordinary words are not companies:** once a model has read the query, it
  decides which companies are named. The rules add only tickers written as
  tickers (`$COST`, `COST`), because "target", "news", "block" and "cost" are
  each an S&P 500 company. A one-letter ticker always needs the `$`; otherwise
  "don't" would name AT&T.
