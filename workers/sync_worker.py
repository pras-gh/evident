"""Sync worker — the S&P 500, fetched once and kept current.

    python -m workers.sync_worker watch               # run forever; this is the one to run
    python -m workers.sync_worker backfill [--limit N]
    python -m workers.sync_worker poll                # one look at EDGAR's latest filings
    python -m workers.sync_worker sweep --since 2026-11-20T00:00:00+00:00
    python -m workers.sync_worker status

`watch` does three things, in this order of priority, round and round:

1. **New filings.** Every `POLL_EVERY`, EDGAR's latest-filings feed for each
   watched form (10-K, 10-Q): one request answers "what's new" for every
   company at once. A filing from a tracked company that is not on record is
   fetched, embedded and rendered straight away. Filings are on the feed
   within minutes of EDGAR accepting them, so with a ten-minute poll a new
   filing is searchable well inside thirty.
2. **Embeddings.** Chunks still without a vector, newest filing first, a
   pass of `EMBED_PASS` at a time — so a new 10-Q is embedded on the next
   turn, ahead of any backlog. A rate limit or spent quota pauses this step
   with a growing wait, and step 3 carries on meanwhile.
3. **Backfill.** Each tracked company with no annual report on record gets its
   latest one, a company at a time. While embedding keeps up, each company is
   searchable before the next is fetched; while it is paused, filings are
   parsed ahead, ready for when the quota allows.

Every fetch is a `company_fetches` row (`source` backfill or watch), carried
out by `fetch_worker.run`, the code a search's fetch runs; the partial unique
index on active rows keeps the worker and a search from fetching one company
twice at once. Each poll and sweep is a `sync_runs` row.

The worker may be away (a laptop asleep, a deploy). When the last good poll is
older than `CATCHUP_AFTER`, it reads each tracked company's own filing list
(a sweep, ~500 requests at EDGAR's rate limit) for anything filed since, then
goes back to polling.

No AI extraction here: memory cards and timelines cost about a dollar a 10-K.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from parallx_db import (FETCH_ACTIVE, Chunk, Company, CompanyFetch, Document, SyncRun,
                        session_scope)
from parallx_parser import edgar
from parallx_parser.universe import Tracked, load

from workers.fetch_worker import ANNUAL_FORMS

log = logging.getLogger("parallx.sync_worker")

#: The forms watched for. 8-Ks are left out: their primary document is a cover
#: page, and what they say is in exhibits this pipeline does not read yet.
WATCH_FORMS = ("10-K", "10-Q")
POLL_EVERY = timedelta(minutes=10)
#: Away longer than this, and the feed may not reach back far enough: sweep.
CATCHUP_AFTER = timedelta(hours=2)
#: How far a feed page or a sweep reaches back past the last good poll.
OVERLAP = timedelta(hours=1)
#: Feed pages read per form per poll at most (100 filings a page).
MAX_PAGES = 10
#: A fetch running longer than this died with its process.
STALE_AFTER = timedelta(minutes=20)
#: A company whose backfill failed is tried again after this.
BACKFILL_RETRY = timedelta(hours=6)
#: A new filing that failed to fetch is tried this many times, a poll apart.
WATCH_ATTEMPTS = 3
#: Chunks embedded per pass of the loop, so a due poll waits for one pass.
EMBED_PASS = 512
EMBED_BACKOFF = (timedelta(minutes=2), timedelta(hours=1))

#: Fetched first, for the companies people ask about most. Order only; when
#: the index changes this goes stale harmlessly.
FIRST = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK-B",
         "JPM", "LLY", "V", "WMT", "XOM", "UNH", "MA", "NFLX", "COST", "ORCL", "AMD")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ claims


def claim(url: str | None, t: Tracked, *, source: str,
          accession: str | None = None) -> int | None:
    """A running `company_fetches` row for `t`, or None when a fetch of that
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
    """Claim and carry out one fetch, in this process. Its status, or None
    when the company was being fetched already."""
    from workers import fetch_worker
    fid = claim(url, t, source=source, accession=accession)
    if fid is None:
        return None
    return fetch_worker.run(fid, url=url, render=render, embed=False)


# ---------------------------------------------------------------- backfill


def backfill_queue(url: str | None, universe: list[Tracked]) -> list[Tracked]:
    """Tracked companies with no annual report on record, `FIRST` first;
    leaving out one whose fetch failed lately, or is under way."""
    with session_scope(url) as db:
        have = set(db.execute(
            select(Company.cik).join(Document, Document.company_id == Company.id)
            .where(Document.form_type.in_(ANNUAL_FORMS))).scalars())
        busy = set(db.execute(select(CompanyFetch.cik).where(
            CompanyFetch.status.in_(FETCH_ACTIVE))).scalars())
        failed = set(db.execute(select(CompanyFetch.cik).where(
            CompanyFetch.status == "failed", CompanyFetch.accession.is_(None),
            CompanyFetch.finished_at > _now() - BACKFILL_RETRY)).scalars())
    todo = [t for t in universe if t.cik not in have | busy | failed]
    rank = {tk: i for i, tk in enumerate(FIRST)}
    return sorted(todo, key=lambda t: min((rank.get(tk, len(rank)) for tk in t.tickers)))


def backfill(url: str | None, universe: list[Tracked], *, limit: int | None = None,
             render: bool = False) -> dict[str, int]:
    out = {"done": 0, "failed": 0, "skipped": 0}
    for t in backfill_queue(url, universe)[:limit]:
        status = fetch(url, t, source="backfill", render=render)
        out[{"done": "done", "failed": "failed"}.get(status or "", "skipped")] += 1
    return out


# -------------------------------------------------------------------- poll


@dataclass
class Found:
    """What one check of EDGAR turned up."""
    seen: int = 0
    new: list[tuple[Tracked, str, str]] = field(default_factory=list)  # (company, accession, form)


def _on_record(url: str | None, accessions: set[str]) -> set[str]:
    if not accessions:
        return set()
    with session_scope(url) as db:
        return set(db.execute(select(Document.accession).where(
            Document.accession.in_(accessions))).scalars())


def _failures(url: str | None) -> dict[str, tuple[str, str, str, int]]:
    """New filings whose fetch failed in the last day, by accession:
    (cik, accession, form, times). EDGAR's filing list can lag its feed by a
    minute, so a failure is worth another try — `WATCH_ATTEMPTS` in all."""
    with session_scope(url) as db:
        rows = db.execute(
            select(CompanyFetch.cik, CompanyFetch.accession, CompanyFetch.form_type,
                   func.count())
            .where(CompanyFetch.source == "watch", CompanyFetch.status == "failed",
                   CompanyFetch.accession.is_not(None),
                   CompanyFetch.requested_at > _now() - timedelta(days=1))
            .group_by(CompanyFetch.cik, CompanyFetch.accession, CompanyFetch.form_type)
        ).all()
    return {acc: (cik, acc, form or "", n) for cik, acc, form, n in rows}


def worth_fetching(url: str | None, tracked: dict[str, Tracked],
                   listed: list[tuple[str, str, str]]) -> list[tuple[Tracked, str, str]]:
    """Of (cik, accession, form) EDGAR listed, plus recent failures: those
    from a tracked company, not on record, and not failed too often."""
    failures = _failures(url)
    candidates = {acc: (cik, acc, form) for cik, acc, form in listed}
    for acc, (cik, _, form, _n) in failures.items():
        candidates.setdefault(acc, (cik, acc, form))
    candidates = {acc: c for acc, c in candidates.items()
                  if c[0] in tracked and failures.get(acc, (0, 0, 0, 0))[3] < WATCH_ATTEMPTS}
    have = _on_record(url, set(candidates))
    return [(tracked[cik], acc, form) for acc, (cik, _, form) in candidates.items()
            if acc not in have]


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
            SyncRun.kind.in_(("poll", "sweep")), SyncRun.error.is_(None),
            SyncRun.finished_at.is_not(None))).scalar()


def _record(url: str | None, kind: str, work) -> SyncRun:
    """Run `work()` → Found as a `sync_runs` row, and fetch what it found."""
    with session_scope(url) as db:
        run = SyncRun(kind=kind, started_at=_now())
        db.add(run)
        db.flush()
        run_id = run.id
    fetched = failed = 0
    error = None
    found = Found()
    try:
        found = work()
        for t, acc, form in found.new:
            status = fetch(url, t, source="watch", accession=acc, render=True)
            if status == "done":
                fetched += 1
                log.info("%s %s %s fetched", t.ticker, form, acc)
            elif status == "failed":
                failed += 1
    except Exception as exc:
        log.exception("%s failed", kind)
        error = f"{type(exc).__name__}: {exc}"[:500]
    with session_scope(url) as db:
        run = db.get(SyncRun, run_id)
        run.finished_at, run.seen, run.new = _now(), found.seen, len(found.new)
        run.fetched, run.failed, run.error = fetched, failed, error
        return run


def poll(url: str | None, universe: list[Tracked]) -> SyncRun:
    """One look at EDGAR's latest filings; fetches what is new."""
    tracked = {t.cik: t for t in universe}

    def work() -> Found:
        entries = read_feed(_last_good(url))
        return Found(seen=len(entries), new=worth_fetching(
            url, tracked, [(e.cik, e.accession, e.form_type) for e in entries]))

    run = _record(url, "poll", work)
    log.info("poll: %d filings listed, %d new, %d fetched, %d failed%s", run.seen,
             run.new, run.fetched, run.failed, f" — {run.error}" if run.error else "")
    return run


def sweep(url: str | None, universe: list[Tracked], since: datetime) -> SyncRun:
    """Each tracked company's own filing list, for watched forms accepted since
    `since`: the catch-up when the worker was away longer than the feed
    reaches back."""
    def work() -> Found:
        found = Found()
        for t in universe:
            try:
                recent = edgar.recent_filings(edgar.fetch_submission(t.cik),
                                              form_types=list(WATCH_FORMS), limit=10)
            except Exception as exc:
                log.warning("sweep: %s: %s", t.ticker, str(exc)[:200])
                continue
            fresh = [(t.cik, f["accession"], f["form_type"])
                     for f in recent if f["published_at"] >= since]
            found.seen += len(fresh)
            found.new += worth_fetching(url, {t.cik: t}, fresh)
        return found

    run = _record(url, "sweep", work)
    log.info("sweep since %s: %d filings, %d new, %d fetched, %d failed", since.isoformat(),
             run.seen, run.new, run.fetched, run.failed)
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
        queue = backfill_queue(self.url, universe)
        if queue:
            t = queue[0]
            log.info("backfill: %s (%s), %d to go", t.ticker, t.name, len(queue) - 1)
            fetch(self.url, t, source="backfill")
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
    w = sub.add_parser("watch", help="run forever: poll, backfill, embed")
    w.add_argument("--every", type=int, default=int(POLL_EVERY.total_seconds() // 60),
                   help="minutes between polls (default %(default)s)")
    b = sub.add_parser("backfill", help="latest annual report for companies missing one")
    b.add_argument("--limit", type=int)
    b.add_argument("--embed", action="store_true", help="embed as it goes")
    sub.add_parser("poll", help="one look at EDGAR's latest filings")
    s = sub.add_parser("sweep", help="each company's own filing list, since a time")
    s.add_argument("--since", required=True, type=datetime.fromisoformat)
    sub.add_parser("status", help="coverage, freshness, and what's pending")
    args = ap.parse_args()

    if args.cmd in ("watch", "backfill", "poll", "sweep") and not edgar.has_user_agent():
        ap.error("set SEC_USER_AGENT (e.g. 'Parallx ingest (you@example.com)'); "
                 "SEC refuses anonymous automated traffic")
    if args.cmd == "watch":
        watch(every=timedelta(minutes=args.every))
    elif args.cmd == "backfill":
        r = backfill(None, load(), limit=args.limit)
        if args.embed:
            r["embedded"] = embed_pass(None, max_chunks=10**9)
        print(json.dumps(r))
    elif args.cmd == "poll":
        r = poll(None, load())
        print(json.dumps({"seen": r.seen, "new": r.new, "fetched": r.fetched,
                          "failed": r.failed, "error": r.error}))
    elif args.cmd == "sweep":
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
