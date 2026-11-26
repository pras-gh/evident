# S&P 500 sync

Parallx tracks the S&P 500. It holds each company's latest annual report and
fetches new 10-Ks and 10-Qs from them as EDGAR accepts them. The goal is that
a new filing is searchable within 30 minutes of acceptance.

```bash
tools/dev.sh sync                              # the long-running worker: all of the below
python -m workers.sync_worker status           # coverage, last checks, latest filings
python -m workers.sync_worker backfill --limit 20
python -m workers.sync_worker poll             # one check of EDGAR's latest filings
python -m workers.sync_worker sweep --since 2026-11-20T00:00:00+00:00
python tools/sp500.py                          # refresh the list when the index changes
```

All of these need `SEC_USER_AGENT` in `.env`, with a real contact, such as
`Parallx ingest (you@example.com)`. EDGAR refuses automated traffic without
one.

## Which companies

The list is `config/sp500.csv`: 503 tickers and 500 companies, taken from
Wikipedia's constituents table, which carries each company's SEC CIK.
Everything keys on the CIK. Tickers change, and share classes repeat:
Alphabet is listed as both GOOGL and GOOG but files once, under one CIK. The
index changes a few times a quarter. When it does, run `tools/sp500.py`; the
worker reads the list on every turn and backfills whoever joined.

## What the worker does

`workers/sync_worker.py watch` does three jobs. On each turn it does the
first one that has work:

1. **Poll for new filings.** Every 10 minutes it reads EDGAR's latest-filings
   feed for each watched form. That is one request per form, covering every
   company at once. A filing from a tracked company that isn't on record is
   fetched and rendered immediately.
2. **Embed.** Chunks that have no vector yet are embedded, 512 at a time,
   newest filing first. A 10-Q filed ten minutes ago therefore jumps ahead of
   any backlog. If the provider refuses (rate limit or spent quota), embedding
   pauses with a growing wait, from 2 minutes up to an hour, and the backfill
   carries on meanwhile.
3. **Backfill.** It fetches the latest annual report (10-K, or 20-F/40-F) of
   each tracked company that has none, one company per turn, starting with the
   most-asked-about. While embedding keeps up, each company becomes searchable
   before the next is fetched. While embedding is paused, filings are parsed
   ahead.

**Catching up.** The feed reaches back only so far. If the worker was away
for more than two hours (a laptop asleep, a deploy), it starts with a sweep.
That reads each tracked company's own filing list (about 500 requests, at
EDGAR's limit of 10 per second) for anything filed since an hour before the
last good check.

**Retries.** A filing can appear on the feed a minute before EDGAR's filing
list shows it. A new filing whose fetch fails is tried again at the next poll,
three times in all. A company whose backfill fails is tried again after six
hours.

**Forms watched.** Only 10-K and 10-Q. Amendments (10-K/A) are left out. 8-Ks
are left out too: their primary document is a cover page, and what they say
is in exhibits this pipeline does not read yet.

**What a fetch skips.** No AI extraction happens here. Memory cards and
timelines cost about a dollar per 10-K, so they are built separately.

## Where it is recorded

- **`company_fetches`:** one row per fetch, whether a search started it
  (`source = search`) or the worker did (`backfill`, `watch`). A partial
  unique index allows only one active fetch per company. That stops a search
  and the worker from fetching the same company at once. A fetch whose
  process died is failed after 20 minutes, and replaced.
- **`sync_runs`:** one row per poll or sweep, with what it saw and fetched.
  The worker uses it to tell how long it was away.
- **`GET /v1/sync`:** reports, from the same query as the `status` command:
  - how many tracked companies are on record and searchable;
  - how many chunks are waiting for a vector;
  - the last poll and sweep;
  - the newest filings fetched, each with how many minutes after acceptance
    it was on record.

## Search, with 500 companies on record

- **On record means searchable:** a company counts once some of its chunks
  are embedded. Naming a company that is parsed but still queued fetches it,
  which embeds it straight away, instead of searching it and finding nothing.
- **Ordinary words are not companies:** once a model has read the query, it
  decides which companies are named. The rules add only tickers written as
  tickers (`$COST`, `COST`), because "target", "news", "block" and "cost" are
  each an S&P 500 company. A one-letter ticker always needs the `$`; otherwise
  "don't" would name AT&T.

## Capacity

A 10-K parses into a few hundred chunks, so the S&P 500's latest annual
reports come to something like 150,000–250,000. EDGAR is not the limit;
embedding quota is. On a free Gemini key the backlog takes days to clear, and
it competes with new filings for the same daily quota. A paid key clears it in
hours, at Gemini's embedding price.
