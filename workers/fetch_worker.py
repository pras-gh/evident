"""Fetch worker — one company's latest annual report, on demand.

A search that names a company with no filings here queues a `company_fetches`
row and starts this, in its own process so the API stays responsive:

    python -m workers.fetch_worker <fetch id>

Steps, each through the code the batch workers use:

1. the latest 10-K (or 20-F / 40-F for a foreign filer) from EDGAR, parsed and
   chunked by `ingest_worker.ingest_ticker`;
2. its chunks embedded by `embedding_worker`, which is what makes it
   searchable. The fetch is `done` here, and the search page shows results;
3. its pages rendered for the evidence viewer, best effort. Until then the
   viewer shows the filing as text.

No AI extraction: memory cards and timelines cost about a dollar a 10-K, and
are left for later.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from evident_db import FETCH_ACTIVE, Company, CompanyFetch, session_scope

log = logging.getLogger("evident.fetch_worker")

#: Annual reports, newest first: a 10-K, or a foreign filer's 20-F / 40-F.
ANNUAL_FORMS = ["10-K", "20-F", "40-F"]


class FetchFailed(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _update(url: str | None, fetch_id: int, **fields) -> None:
    with session_scope(url) as db:
        row = db.get(CompanyFetch, fetch_id)
        for k, v in fields.items():
            setattr(row, k, v)


def run(fetch_id: int, *, url: str | None = None, render: bool = True) -> str:
    """Carry out one fetch; returns its final status. Never raises for a
    fetch that fails — the row records why."""
    from workers import embedding_worker
    from workers.ingest_worker import ingest_ticker

    with session_scope(url) as db:
        row = db.get(CompanyFetch, fetch_id)
        if row is None or row.status not in FETCH_ACTIVE:
            return row.status if row else "missing"
        row.status, row.started_at = "running", _now()
        ticker, cik, name = row.ticker, row.cik, row.name

    try:
        result = ingest_ticker(ticker, form_types=ANNUAL_FORMS, limit=1, url=url)
        if not result.filings:
            raise FetchFailed(f"EDGAR lists no 10-K, 20-F or 40-F for {name}")
        filing = result.filings[0]
        if filing.error:
            raise FetchFailed(f"{filing.form_type} {filing.accession}: {filing.error}")

        with session_scope(url) as db:
            company_id = db.execute(select(Company.id).where(Company.cik == cik)).scalar_one()
        embedding_worker.run(url=url, company_id=company_id)
    except Exception as exc:
        log.exception("fetch %s (%s) failed", fetch_id, ticker)
        _update(url, fetch_id, status="failed", error=str(exc)[:500], finished_at=_now())
        return "failed"

    _update(url, fetch_id, status="done", accession=filing.accession,
            form_type=filing.form_type, chunks=filing.chunks, finished_at=_now())
    log.info("fetch %s: %s %s %s — %d chunks, searchable", fetch_id, ticker,
             filing.form_type, filing.accession, filing.chunks)

    if render:
        try:
            from workers.render_worker import render as render_pages
            for r in render_pages(ticker=ticker, url=url, only_missing=True):
                log.info("rendered %s: %s", r.accession, r.error or f"{r.pages} pages")
        except Exception:               # the viewer falls back to text
            log.exception("rendering %s failed; its filings show as text", ticker)
    return "done"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(0 if run(int(sys.argv[1])) == "done" else 1)
