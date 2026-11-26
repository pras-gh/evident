"""What EDGAR says about a company, before any filing is read.

* `Filing` — the filing index: every 10-K, 10-Q and 8-K a tracked company
  files, from the Submissions API. Metadata only; a filing's text is
  downloaded on demand into `documents`, under the same accession.
* `FinancialFact` — its financial statements, from the Company Facts API:
  one value per statement line per period (see `parallx_parser.facts`).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer,
                        Numeric, SmallInteger, String, Text, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

#: Forms kept in the filing index. The rest of a company's list — insider
#: trades (Form 4), registration statements, proxy filings — is left out.
INDEX_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A",
                         "20-F", "20-F/A", "40-F", "40-F/A", "6-K"})


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (
        Index("ix_filings_company_id_accepted_at", "company_id", "accepted_at"),
        Index("ix_filings_accepted_at", "accepted_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    accession: Mapped[str] = mapped_column(String(32), unique=True)
    form_type: Mapped[str] = mapped_column(String(16))
    filed_date: Mapped[date] = mapped_column(Date)
    #: when EDGAR accepted it: when it became public
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    #: the period it reports on
    report_date: Mapped[Optional[date]] = mapped_column(Date)
    primary_document: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    #: an 8-K's items, "2.02,9.01" — 2.02 is results of operations
    items: Mapped[Optional[str]] = mapped_column(String(128))
    size: Mapped[Optional[int]] = mapped_column(Integer)
    is_xbrl: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    #: when Parallx first knew of it; less `accepted_at`, how far behind it was
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"))
    #: when an alert went out for it; null for filings indexed in a backfill
    alerted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint("company_id", "concept", "unit", "frame",
                         name="uq_financial_facts_company_id_concept_unit_frame"),
        Index("ix_financial_facts_company_id_period_end", "company_id", "period_end"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    #: "us-gaap:Revenues"
    concept: Mapped[str] = mapped_column(String(160))
    unit: Mapped[str] = mapped_column(String(32))
    #: null for a balance at a date
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 6))
    #: the filing that reported it
    accession: Mapped[str] = mapped_column(String(32))
    form: Mapped[str] = mapped_column(String(16))
    filed: Mapped[date] = mapped_column(Date)
    fiscal_year: Mapped[Optional[int]] = mapped_column(SmallInteger)
    fiscal_period: Mapped[Optional[str]] = mapped_column(String(4))
    #: EDGAR's calendar period: CY2025, CY2025Q3, CY2025Q3I
    frame: Mapped[str] = mapped_column(String(16))


# ------------------------------------------------------------------ writes

def store_filings(db, company_id: int, rows: list[dict]) -> list[str]:
    """Adds the filings not indexed yet, of `INDEX_FORMS`; returns the
    accessions that were new. `rows` are `parallx_parser.edgar.filing_rows`."""
    from sqlalchemy.dialects.postgresql import insert
    keep = [r for r in rows if r["form_type"] in INDEX_FORMS]
    new: list[str] = []
    for i in range(0, len(keep), 500):
        batch = [dict(company_id=company_id, accession=r["accession"],
                      form_type=r["form_type"], filed_date=r["filed_date"],
                      accepted_at=r["published_at"],
                      report_date=(date.fromisoformat(r["report_date"])
                                   if isinstance(r.get("report_date"), str)
                                   else r.get("report_date")),
                      primary_document=r.get("primary_document"),
                      description=r.get("description"), items=r.get("items"),
                      size=r.get("size"), is_xbrl=bool(r.get("is_xbrl")))
                 for r in keep[i:i + 500]]
        new += db.execute(insert(Filing).values(batch)
                          .on_conflict_do_nothing(index_elements=["accession"])
                          .returning(Filing.accession)).scalars().all()
    return new


def store_facts(db, company_id: int, rows: list[dict]) -> int:
    """Upserts facts by (company, concept, unit, frame): a later filing that
    restates a period replaces the value, and says so in `accession`.
    Returns how many rows were written. `rows` are `parallx_parser.facts.Fact`
    as dicts."""
    from sqlalchemy.dialects.postgresql import insert
    # one row per key: EDGAR assigns each frame once, but a payload is data
    by_key = {(r["concept"], r["unit"], r["frame"]): r for r in rows}
    written = 0
    values = [dict(company_id=company_id, **r) for r in by_key.values()]
    for i in range(0, len(values), 1000):
        stmt = insert(FinancialFact).values(values[i:i + 1000])
        ex = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            constraint="uq_financial_facts_company_id_concept_unit_frame",
            set_={c: getattr(ex, c) for c in ("period_start", "period_end", "value",
                                              "accession", "form", "filed",
                                              "fiscal_year", "fiscal_period")},
            where=(FinancialFact.accession != ex.accession) | (FinancialFact.value != ex.value)
        ).returning(FinancialFact.id)
        # counted from RETURNING: a multi-row upsert's rowcount is not reported
        written += len(db.execute(stmt).scalars().all())
    return written
