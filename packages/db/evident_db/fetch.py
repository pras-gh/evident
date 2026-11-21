"""On-demand fetches of a company's filings from EDGAR.

A search that names a company with no filings here starts one: the latest
annual report is downloaded, parsed, chunked and embedded by
`workers/fetch_worker.py`, in its own process. The row is how the search page
knows to say "fetching", how a second search for the same company joins the
first fetch instead of starting another, and what is left to read when one
fails.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

FETCH_STATUSES = ("queued", "running", "done", "failed")
#: A fetch in one of these is still under way; there is at most one per company.
ACTIVE = ("queued", "running")


class CompanyFetch(Base):
    __tablename__ = "company_fetches"
    __table_args__ = (
        CheckConstraint("status in ('queued','running','done','failed')", name="status"),
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
    error: Mapped[Optional[str]] = mapped_column(Text)
    #: what was fetched, once it was
    accession: Mapped[Optional[str]] = mapped_column(String(32))
    form_type: Mapped[Optional[str]] = mapped_column(String(16))
    chunks: Mapped[Optional[int]] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
