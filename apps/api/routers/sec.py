"""What EDGAR lists, before any filing is read: the filing index, and
financial statements from XBRL.

    GET /v1/filings/latest                    the newest filings, every company
    GET /v1/companies/{ticker}/filings        one company's
    GET /v1/companies/{ticker}/financials     its statements, annual or quarterly

Kept current by `workers/sync_worker.py`: the index from the Submissions API,
statements from the Company Facts API. A statement value is cited to the
filing that reported it and its XBRL concept — XBRL tags numbers, not
sentences, so there is no paragraph to point at; the filing's text is
downloaded on demand.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import Company, Filing, FinancialFact

from ..deps import get_company, get_db
from ..schemas import (FilingOut, FinancialCellOut, FinancialColumnOut, FinancialRowOut,
                       FinancialsOut)

router = APIRouter(tags=["filings"])


def filing_out(row: dict) -> FilingOut:
    from parallx_parser.edgar import public_url
    from parallx_parser.forms import about
    return FilingOut(
        **{k: row[k] for k in ("ticker", "company", "cik", "accession", "form_type",
                               "filed_date", "accepted_at", "report_date", "items",
                               "description", "alerted", "minutes_after_filing", "loaded")},
        about=about(row["form_type"], row["items"], row["report_date"]),
        url=public_url(row["cik"], row["accession"], row["primary_document"]))


def _forms(forms: str | None) -> list[str] | None:
    return [f.strip().upper() for f in forms.split(",") if f.strip()] if forms else None


@router.get("/filings/latest", response_model=list[FilingOut],
            summary="The newest filings, across companies")
async def latest(limit: int = Query(20, ge=1, le=200),
                 forms: str | None = Query(None, description="e.g. 10-K,10-Q,8-K"),
                 days: int | None = Query(None, ge=1, le=3650,
                                          description="accepted within this many days"),
                 db: AsyncSession = Depends(get_db)) -> list[FilingOut]:
    from datetime import datetime, timedelta, timezone
    from parallx_db.sync import latest_filings
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = await db.run_sync(lambda s: latest_filings(s, forms=_forms(forms), since=since,
                                                      limit=limit))
    return [filing_out(r) for r in rows]


@router.get("/companies/{ticker}/filings", response_model=list[FilingOut],
            summary="A company's filings")
async def company_filings(limit: int = Query(40, ge=1, le=500),
                          forms: str | None = Query(None, description="e.g. 10-K,10-Q"),
                          company: Company = Depends(get_company),
                          db: AsyncSession = Depends(get_db)) -> list[FilingOut]:
    from parallx_db.sync import latest_filings
    rows = await db.run_sync(lambda s: latest_filings(
        s, company_id=company.id, forms=_forms(forms), limit=limit))
    return [filing_out(r) for r in rows]


@router.get("/companies/{ticker}/financials", response_model=FinancialsOut,
            summary="A company's financial statements")
async def financials(period: Literal["annual", "quarterly"] = "annual",
                     periods: int = Query(5, ge=1, le=12),
                     company: Company = Depends(get_company),
                     db: AsyncSession = Depends(get_db)) -> FinancialsOut:
    from parallx_parser.edgar import public_url
    from parallx_parser.facts import METRICS, statement
    facts = list((await db.execute(select(FinancialFact).where(
        FinancialFact.company_id == company.id))).scalars())
    st = statement(facts, period=period, periods=periods)
    cited = {c.accession for cells in st.rows.values() for c in cells if c}
    docs = dict((await db.execute(select(Filing.accession, Filing.primary_document).where(
        Filing.accession.in_(cited)))).all()) if cited else {}

    def cell(c) -> FinancialCellOut | None:
        if c is None:
            return None
        return FinancialCellOut(value=float(c.value), concept=c.concept, accession=c.accession,
                                form=c.form, filed=c.filed, derived=c.derived,
                                url=public_url(company.cik, c.accession, docs.get(c.accession)))

    rows = [FinancialRowOut(key=m.key, label=m.label, unit=m.unit,
                            cells=[cell(c) for c in st.rows[m.key]])
            for m in METRICS if any(st.rows[m.key])]
    return FinancialsOut(
        ticker=company.ticker, period=period,
        columns=[FinancialColumnOut(label=c.label, period_start=c.period_start,
                                    period_end=c.period_end) for c in st.columns],
        rows=rows, loaded_at=company.facts_loaded_at)
