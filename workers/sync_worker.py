"""Sync worker — the S&P 500 from EDGAR's APIs, kept current.

    python -m workers.sync_worker watch               # run forever; this is the one to run
    python -m workers.sync_worker backfill [--limit N] [--documents N]
    python -m workers.sync_worker poll                # one look at what was just filed
    python -m workers.sync_worker sweep --since 2026-11-20T00:00:00+00:00
    python -m workers.sync_worker status

Where each thing comes from:

| what                      | from                                    | when                     |
| ------------------------- | --------------------------------------- | ------------------------ |
| a company's filing list   | Submissions API                         | backfill; when it files  |
| financial statements      | Company Facts API                       | backfill; on a 10-K/10-Q |
| what was just filed       | the latest-filings feed (RSS), polled   | every `POLL_EVERY`       |
| a filing's text           | EDGAR Archives (HTML)                   | on demand                |
| history, for backtesting  | the nightly bulk ZIPs                   | `tools/sec_bulk.py`      |

A filing's text is downloaded when someone asks for it — a search naming the
company — not for all 500 up front: parsing and embedding every annual report
would take days of embedding quota for companies nobody has asked about.
Once a company's filings are loaded, its new 10-Ks and 10-Qs are downloaded
as they are filed, so what people use stays current.

`watch` does, in this order of priority, round and round:

1. **Poll.** Every `POLL_EVERY` (10 minutes), the feed for each watched form:
   one request says what every company filed lately. For each tracked company
   with a filing not indexed yet: its filing list from the Submissions API,
   its statements from Company Facts if a 10-K or 10-Q is new, the new
   filing's text if the company's filings are loaded, and an alert
   (`workers/alerts.py`).
2. **Embed.** Chunks without a vector, newest filing first. A rate limit or
   spent quota pauses this with a growing wait.
3. **Backfill.** A tracked company never indexed: its filing list and its
   statements, two requests, one company per turn. All 500 take minutes.

When the last good poll is older than `CATCHUP_AFTER` (a laptop asleep, a
deploy), the feed may not reach back far enough: the worker sweeps first,
reading each company's filing list for anything accepted since.

No AI extraction here: memory cards and timelines cost about a dollar a 10-K.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from parallx_db import (FETCH_ACTIVE, Chunk, Company, CompanyFetch, Document, Filing,
                        SyncRun, session_scope)
from parallx_db.repositories import upsert_company
from parallx_db.sec import store_facts, store_filings
from parallx_parser import edgar
from parallx_parser.facts import PERIODIC, facts as framed_facts
from parallx_parser.universe import Tracked, load

from workers import alerts
from workers.fetch_worker import ANNUAL_FORMS

log = logging.getLogger("parallx.sync_worker")

#: Forms polled for on the feed. 8-Ks are indexed and alerted on, but their
#: text is not downloaded: the primary document is a cover page, and what
#: they say is in exhibits this pipeline does not read yet.
WATCH_FORMS = ("10-K", "10-Q", "8-K")
#: New filings of these are downloaded for companies whose filings are loaded.
DOWNLOAD_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F"})
POLL_EVERY = timedelta(minutes=10)
#: Away longer than this, and the feed may not reach back far enough: sweep.
CATCHUP_AFTER = timedelta(hours=2)
#: How far a feed read or a sweep reaches back past the last good poll.
OVERLAP = timedelta(hours=1)
#: Feed pages read per form per poll at most (100 filings a page).
MAX_PAGES = 10
#: A fetch running longer than this died with its process.
STALE_AFTER = timedelta(minutes=20)
#: A company that could not be indexed is tried again after this.
INDEX_RETRY = timedelta(hours=6)
#: A new filing whose download failed is tried this many times, a poll apart.
DOWNLOAD_ATTEMPTS = 3
#: Only filings accepted this recently alert; older ones found on a first
#: indexing or a long catch-up are history, not news.
ALERT_WINDOW = timedelta(days=2)
#: Chunks embedded per pass of the loop, so a due poll waits for one pass.
EMBED_PASS = 512
EMBED_BACKOFF = (timedelta(minutes=2), timedelta(hours=1))

#: Indexed first: the companies people ask about most. Order only.
FIRST = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B",
         "JPM", "LLY", "V", "WMT", "XOM", "UNH", "MA", "NFLX", "COST", "ORCL", "AMD")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ranked(companies: list[Tracked]) -> list[Tracked]:
    rank = {tk: i for i, tk in enumerate(FIRST)}
    return sorted(companies, key=lambda t: min(rank.get(tk, len(rank)) for tk in t.tickers))


# --------------------------------------------------------- one company


@dataclass
class Refreshed:
    ticker: str
    new: list[str] = field(default_factory=list)          # accessions indexed now
    facts: int = 0                                         # fact rows written
    fetched: list[str] = field(default_factory=list)      # filings downloaded
    alerted: list[str] = field(default_factory=list)


def refresh(url: str | None, t: Tracked, *, history: bool = False, pages=None,
            submission: dict | None = None, facts_payload: dict | None = None,
            alert_since: datetime | None = None, download: bool = True,
            just_filed: set[str] | None = None) -> Refreshed:
    """Brings one company up to date with EDGAR: its filing list (Submissions
    API), its statements (Company Facts, when a periodic report is new or
    none are loaded), the text of a new 10-K/10-Q when its filings are
    loaded, and alerts for new filings accepted since `alert_since`.

    A company's first indexing adds its whole recent history, which is not
    news: nothing alerts or downloads then, except what the feed just listed
    (`just_filed`).

    `history` adds the filings older than the ~1,000 recent ones, from the
    submission's older pages (read by `pages`, name → page; the API by
    default). `submission`, `facts_payload` and `pages` stand in for the API:
    the bulk loader passes what it read from the ZIPs."""
    submission = submission or edgar.fetch_submission(t.cik)
    rows = edgar.filing_rows(submission["filings"]["recent"])
    if history:
        for page in submission["filings"].get("files") or []:
            rows += edgar.filing_rows((pages or edgar.fetch_submission_page)(page["name"]))
    listed = submission.get("tickers") or list(t.tickers)
    with session_scope(url) as db:
        company = upsert_company(db, cik=t.cik, name=submission.get("name") or t.name,
                                 ticker=listed[0], sic=submission.get("sic"))
        company_id, first = company.id, company.filings_indexed_at is None
        out = Refreshed(listed[0], new=store_filings(db, company_id, rows))
        company.filings_indexed_at = _now()
        need_facts = company.facts_loaded_at is None
        loaded = db.execute(select(Document.id).where(Document.company_id == company_id)
                            .limit(1)).first() is not None
    new_rows = {r["accession"]: r for r in rows if r["accession"] in set(out.new)}
    news = {acc: r for acc, r in new_rows.items()
            if not first or acc in (just_filed or set())}

    if need_facts or facts_payload is not None or any(
            r["form_type"] in PERIODIC for r in new_rows.values()):
        out.facts = load_facts(url, t.cik, company_id, payload=facts_payload)

    if download and loaded:
        for acc, r in sorted(news.items(), key=lambda kv: kv[1]["published_at"]):
            if r["form_type"] in DOWNLOAD_FORMS and fetch(
                    url, t, source="watch", accession=acc, render=True) == "done":
                out.fetched.append(acc)

    if alert_since is not None:
        out.alerted = alerts.send([
            alerts.NewFiling(ticker=out.ticker, company=t.name, cik=t.cik, accession=acc,
                             form_type=r["form_type"], accepted_at=r["published_at"],
                             report_date=_date(r.get("report_date")), items=r.get("items"),
                             primary_document=r.get("primary_document"))
            for acc, r in news.items() if r["published_at"] >= alert_since])
        if out.alerted:
            with session_scope(url) as db:
                db.query(Filing).filter(Filing.accession.in_(out.alerted)).update(
                    {Filing.alerted_at: _now()}, synchronize_session=False)
    return out


def _date(v):
    from datetime import date
    return date.fromisoformat(v) if isinstance(v, str) and v else None


def load_facts(url: str | None, cik: str, company_id: int, *,
               payload: dict | None = None) -> int:
    """The company's statements from Company Facts; fact rows written. A
    company with no XBRL facts (404) has none, and is marked loaded."""
    try:
        payload = payload if payload is not None else edgar.fetch_company_facts(cik)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        payload = {}
    rows = [dataclasses.asdict(f) for f in framed_facts(payload)]
    with session_scope(url) as db:
        written = store_facts(db, company_id, rows)
        db.get(Company, company_id).facts_loaded_at = _now()
    return written


# ---------------------------------------------------- downloads (on demand)


def claim(url: str | None, t: Tracked, *, source: str,
          accession: str | None = None) -> int | None:
    """A queued `company_fetches` row for `t`, or None when a fetch of that
    company is under way already (a search's, or another worker's). A row
    left running by a process that died is failed, and the claim retried."""
    for _ in range(2):
        with session_scope(url) as db:
            fid = db.execute(
                insert(CompanyFetch).values(
                    cik=t.cik, ticker=t.ticker, name=t.name, status="queued",
                    source=source, accession=accession)
                .on_conflict_do_nothing(index_elements=["cik"],
                                        index_where=text("status in ('queued','running')"))
                .returning(CompanyFetch.id)).scalar_one_or_none()
            if fid is not None:
                return fid
            active = db.execute(select(CompanyFetch).where(
                CompanyFetch.cik == t.cik,
                CompanyFetch.status.in_(FETCH_ACTIVE))).scalar_one_or_none()
            if active is None:
                continue                      # it finished between the two statements
            if _now() - (active.started_at or active.requested_at) < STALE_AFTER:
                return None
            active.status, active.error, active.finished_at = (
                "failed", "stopped without finishing", _now())
    return None


def fetch(url: str | None, t: Tracked, *, source: str, accession: str | None = None,
          render: bool = False) -> str | None:
    """Claim and carry out one download, in this process: the filing named,
    else the latest annual report. Its status, or None when the company was
    being fetched already. Embedded later, by the loop."""
    from workers import fetch_worker
    fid = claim(url, t, source=source, accession=accession)
    if fid is None:
        return None
    return fetch_worker.run(fid, url=url, render=render, embed=False)


def retry_downloads(url: str | None, tracked: dict[str, Tracked]) -> list[str]:
    """New 10-Ks/10-Qs of loaded companies whose download failed lately,
    tried again — up to `DOWNLOAD_ATTEMPTS` times in all."""
    with session_scope(url) as db:
        rows = db.execute(
            select(CompanyFetch.cik, CompanyFetch.accession, func.count())
            .where(CompanyFetch.source == "watch", CompanyFetch.status == "failed",
                   CompanyFetch.accession.is_not(None),
                   CompanyFetch.requested_at > _now() - timedelta(days=1),
                   ~select(Document.id).where(
                       Document.accession == CompanyFetch.accession).exists())
            .group_by(CompanyFetch.cik, CompanyFetch.accession)).all()
    done = []
    for cik, acc, n in rows:
        if cik in tracked and n < DOWNLOAD_ATTEMPTS and fetch(
                url, tracked[cik], source="watch", accession=acc, render=True) == "done":
            done.append(acc)
    return done


# ---------------------------------------------------------------- backfill


def backfill_queue(url: str | None, universe: list[Tracked], *,
                   failed: dict[str, datetime] | None = None) -> list[Tracked]:
    """Tracked companies whose filing list or statements were never read;
    leaving out any that failed within `INDEX_RETRY`."""
    with session_scope(url) as db:
        done = set(db.execute(select(Company.cik).where(
            Company.filings_indexed_at.is_not(None),
            Company.facts_loaded_at.is_not(None))).scalars())
    cutoff = _now() - INDEX_RETRY
    return _ranked([t for t in universe if t.cik not in done
                    and (failed or {}).get(t.cik, cutoff) <= cutoff])


def backfill(url: str | None, universe: list[Tracked], *, limit: int | None = None,
             documents: int = 0) -> dict[str, int]:
    """Indexes every tracked company not indexed yet (filing list and
    statements), and downloads the latest annual report of the first
    `documents` of them, for a demo that should not wait on a search."""
    out = {"indexed": 0, "failed": 0, "documents": 0}
    for t in backfill_queue(url, universe)[:limit]:
        try:
            refresh(url, t)
            out["indexed"] += 1
        except Exception as exc:
            log.warning("backfill: %s: %s", t.ticker, str(exc)[:200])
            out["failed"] += 1
    if documents:
        with session_scope(url) as db:
            loaded = set(db.execute(
                select(Company.cik).join(Document, Document.company_id == Company.id)
                .where(Document.form_type.in_(ANNUAL_FORMS))).scalars())
        for t in [t for t in _ranked(universe) if t.cik not in loaded][:documents]:
            if fetch(url, t, source="backfill") == "done":
                out["documents"] += 1
    return out


# -------------------------------------------------------------- poll, sweep


def read_feed(since: datetime | None) -> list[edgar.LatestFiling]:
    """The feed's filings of the watched forms back to `since` (a page per
    form when there is no `since`), newest first."""
    out: list[edgar.LatestFiling] = []
    for form in WATCH_FORMS:
        for page in range(MAX_PAGES):
            entries = edgar.latest_filings(form, start=page * 100)
            out.extend(entries)
            oldest = min((e.accepted_at for e in entries if e.accepted_at), default=None)
            if (len(entries) < 100 or since is None or oldest is None
                    or oldest <= since - OVERLAP):
                break
    return out


def _last_good(url: str | None) -> datetime | None:
    with session_scope(url) as db:
        return db.execute(select(func.max(SyncRun.started_at)).where(
            SyncRun.error.is_(None), SyncRun.finished_at.is_not(None))).scalar()


def _indexed(url: str | None, accessions: set[str]) -> set[str]:
    if not accessions:
        return set()
    with session_scope(url) as db:
        return set(db.execute(select(Filing.accession).where(
            Filing.accession.in_(accessions))).scalars())


@dataclass
class Found:
    """What one check of EDGAR turned up: how many filings it listed, and the
    tracked companies with something new."""
    seen: int = 0
    companies: list[Tracked] = field(default_factory=list)
    #: per CIK, the accessions the feed listed
    listed: dict[str, set[str]] = field(default_factory=dict)


def _record(url: str | None, kind: str, work, *, alert_since: datetime) -> SyncRun:
    """Run `work()` → Found as a `sync_runs` row, and refresh each company it
    found: `new` counts filings indexed, `fetched` filings downloaded,
    `failed` companies EDGAR could not serve."""
    with session_scope(url) as db:
        run = SyncRun(kind=kind, started_at=_now())
        db.add(run)
        db.flush()
        run_id = run.id
    new = fetched = failed = 0
    error = None
    found = Found()
    try:
        found = work()
        for t in found.companies:
            try:
                r = refresh(url, t, alert_since=alert_since,
                            just_filed=found.listed.get(t.cik))
            except Exception as exc:
                log.warning("%s: %s: %s", kind, t.ticker, str(exc)[:200])
                failed += 1
                continue
            new, fetched = new + len(r.new), fetched + len(r.fetched)
            for acc in r.new:
                log.info("%s: %s indexed %s", kind, r.ticker, acc)
        fetched += len(retry_downloads(url, {t.cik: t for t in load()}))
    except Exception as exc:
        log.exception("%s failed", kind)
        error = f"{type(exc).__name__}: {exc}"[:500]
    with session_scope(url) as db:
        run = db.get(SyncRun, run_id)
        run.finished_at, run.seen, run.new = _now(), found.seen, new
        run.fetched, run.failed, run.error = fetched, failed, error
        return run


def poll(url: str | None, universe: list[Tracked]) -> SyncRun:
    """One look at EDGAR's latest-filings feed; brings each tracked company
    with a filing not indexed yet up to date."""
    tracked = {t.cik: t for t in universe}
    last = _last_good(url)

    def work() -> Found:
        entries = [e for e in read_feed(last) if e.cik in tracked]
        known = _indexed(url, {e.accession for e in entries})
        fresh = [e for e in entries if e.accession not in known]
        listed: dict[str, set[str]] = {}
        for e in fresh:
            listed.setdefault(e.cik, set()).add(e.accession)
        return Found(seen=len(entries), companies=[tracked[c] for c in listed],
                     listed=listed)

    # alert on what was filed since the last look; on a first poll, the last
    # half hour rather than a page of the last day
    since = (last - OVERLAP) if last else _now() - timedelta(minutes=30)
    run = _record(url, "poll", work, alert_since=max(since, _now() - ALERT_WINDOW))
    log.info("poll: %d filings from tracked companies listed, %d new, %d downloaded, "
             "%d companies failed%s", run.seen, run.new, run.fetched, run.failed,
             f" — {run.error}" if run.error else "")
    return run


def sweep(url: str | None, universe: list[Tracked], since: datetime) -> SyncRun:
    """Every tracked company's filing list, read again: the catch-up when the
    worker was away longer than the feed reaches back. Filings accepted since
    `since` alert."""
    run = _record(url, "sweep", lambda: Found(seen=0, companies=list(universe)),
                  alert_since=max(since, _now() - ALERT_WINDOW))
    log.info("sweep since %s: %d new filings, %d downloaded, %d companies failed",
             since.isoformat(), run.new, run.fetched, run.failed)
    return run


# --------------------------------------------------------------- embedding


def embed_pass(url: str | None, *, max_chunks: int = EMBED_PASS) -> int:
    """Embeds up to `max_chunks` chunks, newest filing first. Raises
    `EmbeddingError` when the provider refuses (rate limit, quota); what was
    embedded before that is kept."""
    from workers import embedding_worker
    return embedding_worker.run(url=url, max_chunks=max_chunks).embedded


def pending_chunks(url: str | None) -> int:
    with session_scope(url) as db:
        return db.execute(select(func.count()).select_from(Chunk)
                          .where(Chunk.embedding.is_(None))).scalar_one()


# ------------------------------------------------------------------- watch


class Watch:
    """The loop. Split out from `watch` so a test can turn it by hand."""

    def __init__(self, url: str | None, *, every: timedelta = POLL_EVERY,
                 clock=_now, sleep=time.sleep) -> None:
        self.url, self.every, self.clock, self.sleep = url, every, clock, sleep
        self.next_poll = clock()
        self.embed_after = clock()
        self.embed_wait = EMBED_BACKOFF[0]
        self.index_failed: dict[str, datetime] = {}
        self.stopping = False

    def catch_up(self, universe: list[Tracked]) -> None:
        last = _last_good(self.url)
        if last is not None and self.clock() - last > CATCHUP_AFTER:
            log.info("last good check of EDGAR was %s; sweeping for what was filed since",
                     last.isoformat())
            sweep(self.url, universe, since=last - OVERLAP)

    def turn(self) -> str:
        """One step of the loop; says which."""
        universe = load()                  # tools/sp500.py may have refreshed it
        if self.clock() >= self.next_poll:
            poll(self.url, universe)
            self.next_poll = self.clock() + self.every
            return "poll"
        if self.clock() >= self.embed_after and pending_chunks(self.url):
            from parallx_retrieval.embed import EmbeddingError
            try:
                n = embed_pass(self.url)
            except EmbeddingError as exc:
                self.embed_after = self.clock() + self.embed_wait
                log.warning("embedding paused for %s: %s", self.embed_wait, str(exc)[:300])
                self.embed_wait = min(self.embed_wait * 2, EMBED_BACKOFF[1])
                return "embed-paused"
            self.embed_wait = EMBED_BACKOFF[0]
            log.info("embedded %d chunks; %d to go", n, pending_chunks(self.url))
            return "embed"
        queue = backfill_queue(self.url, universe, failed=self.index_failed)
        if queue:
            t = queue[0]
            try:
                r = refresh(self.url, t)
                log.info("indexed %s: %d filings, %d facts; %d companies to go",
                         r.ticker, len(r.new), r.facts, len(queue) - 1)
            except Exception as exc:
                self.index_failed[t.cik] = self.clock()
                log.warning("indexing %s failed: %s", t.ticker, str(exc)[:200])
            return "backfill"
        return "idle"

    def run(self) -> None:
        self.catch_up(load())
        while not self.stopping:
            try:
                step = self.turn()
            except Exception:              # a bad turn must not end the watch
                log.exception("sync turn failed")
                step = "error"
            if step in ("idle", "error"):
                # nothing to do until the next poll, or the embedding pause ends
                wake = self.next_poll
                if self.embed_after > self.clock():
                    wake = min(wake, self.embed_after)
                self.sleep(max(1.0, min(60.0, (wake - self.clock()).total_seconds())))


def watch(url: str | None = None, *, every: timedelta = POLL_EVERY) -> None:
    w = Watch(url, every=every)

    def stop(*_):
        log.info("stopping after this step")
        w.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("watching %d companies; polling EDGAR every %s", len(load()), every)
    w.run()


# ------------------------------------------------------------------ status


def status(url: str | None = None) -> dict:
    from parallx_db.sync import status as db_status
    with session_scope(url) as db:
        return db_status(db, [t.cik for t in load()])


def main() -> int:
    ap = argparse.ArgumentParser(prog="parallx-sync", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="run forever: poll, embed, backfill")
    w.add_argument("--every", type=int, default=int(POLL_EVERY.total_seconds() // 60),
                   help="minutes between polls (default %(default)s)")
    b = sub.add_parser("backfill", help="filing lists and statements for companies missing them")
    b.add_argument("--limit", type=int)
    b.add_argument("--documents", type=int, default=0, metavar="N",
                   help="also download the latest annual report of the first N companies")
    sub.add_parser("poll", help="one look at EDGAR's latest filings")
    s = sub.add_parser("sweep", help="every company's filing list, since a time")
    s.add_argument("--since", required=True, type=datetime.fromisoformat)
    sub.add_parser("status", help="coverage, freshness, and what's pending")
    args = ap.parse_args()

    if args.cmd != "status" and not edgar.has_user_agent():
        ap.error("set SEC_USER_AGENT (e.g. 'Parallx ingest (you@example.com)'); "
                 "SEC refuses anonymous automated traffic")
    if args.cmd == "watch":
        watch(every=timedelta(minutes=args.every))
    elif args.cmd == "backfill":
        print(json.dumps(backfill(None, load(), limit=args.limit, documents=args.documents)))
    elif args.cmd in ("poll", "sweep"):
        if args.cmd == "poll":
            r = poll(None, load())
        else:
            since = args.since if args.since.tzinfo else args.since.replace(tzinfo=timezone.utc)
            r = sweep(None, load(), since)
        print(json.dumps({"seen": r.seen, "new": r.new, "fetched": r.fetched,
                          "failed": r.failed, "error": r.error}))
    else:
        print(json.dumps(status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
