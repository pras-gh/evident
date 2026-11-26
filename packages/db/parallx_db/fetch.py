"""Fetches of a company's filings from EDGAR, and the checks that start them.

A fetch is started three ways (`source`):

* `search` — a search names a company with no filings here; its latest
  annual report is fetched on demand;
* `backfill` — the sync worker fills in an S&P 500 company's latest annual
  report;
* `watch` — the sync worker saw a new 10-K or 10-Q from a tracked company in
  EDGAR's latest-filings feed; `accession` names it from the start.

`workers/fetch_worker.py` carries every one out. The row is how the search
page knows to say "fetching", how a second request for the same company joins
the fetch under way instead of starting another, and what is left to read when
one fails. `SyncRun` records each check of EDGAR the sync worker makes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

FETCH_STATUSES = ("queued", "running", "done", "failed")
FETCH_SOURCES = ("search", "backfill", "watch")
SYNC_KINDS = ("poll", "sweep")
#: A fetch in one of these is still under way; there is at most one per company.
ACTIVE = ("queued", "running")


class CompanyFetch(Base):
    __tablename__ = "company_fetches"
    __table_args__ = (
        CheckConstraint("status in ('queued','running','done','failed')", name="status"),
        CheckConstraint("source in ('search','backfill','watch')", name="source"),
        # One fetch under way per company: a second search joins it.
        Index("uq_company_fetches_active_cik", "cik", unique=True,
              postgresql_where=text("status in ('queued','running')")),
        Index("ix_company_fetches_cik_requested_at", "cik", "requested_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cik: Mapped[str] = mapped_column(String(10))
    ticker: Mapped[Optional[str]] = mapped_column(String(16))
    #: EDGAR's name for the company
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    source: Mapped[str] = mapped_column(String(16), default="search",
                                        server_default="search")
    error: Mapped[Optional[str]] = mapped_column(Text)
    #: the filing fetched: set once it was, or from the start when one
    #: particular filing was asked for (a `watch` fetch)
    accession: Mapped[Optional[str]] = mapped_column(String(32))
    form_type: Mapped[Optional[str]] = mapped_column(String(16))
    chunks: Mapped[Optional[int]] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    """One check of EDGAR for new filings from the companies tracked: `poll`
    reads the latest-filings feed, `sweep` each company's own filing list
    (after the worker was down longer than the feed reaches back)."""
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint("kind in ('poll','sweep')", name="kind"),
        Index("ix_sync_runs_kind_started_at", "kind", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    #: filings of the watched forms EDGAR listed
    seen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: ...of those, from a tracked company and not on record yet
    new: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fetched: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: why the check itself failed (EDGAR unreachable); per-filing failures
    #: are on their fetch rows
    error: Mapped[Optional[str]] = mapped_column(Text)
