"""Ingestion endpoint."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["ingest"])


class IngestRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=8, examples=["NVDA"])
    form_types: list[str] | None = Field(
        None, examples=[["10-K", "10-Q"]],
        description="omit to take whatever was filed most recently")
    limit: int = Field(1, ge=1, le=20, description="how many filings to pull")


class FilingOut(BaseModel):
    accession: str
    form_type: str
    filed_at: str
    skipped: bool
    pages: int
    sections: int
    chunks: int
    tables: int
    error: str | None = None


class IngestResponse(BaseModel):
    ticker: str
    cik: str
    company: str
    chunks_written: int
    filings: list[FilingOut]


@router.post("/ingest", response_model=IngestResponse, summary="Ingest filings")
async def ingest(req: IngestRequest) -> IngestResponse:
    """Download, parse and chunk a company's most recent filings.

    Runs inline and is bounded by `limit`, which is honest for the sizes V1
    handles but is the obvious thing to move onto a queue: a twenty-filing
    backfill will outlive a sensible HTTP timeout.

    Idempotent — a filing whose bytes are unchanged is recognised and skipped
    before it is parsed.
    """
    import anyio

    from workers.ingest_worker import ingest_ticker

    if not os.environ.get("SEC_USER_AGENT"):
        raise HTTPException(
            503,
            "SEC_USER_AGENT is not set. SEC requires automated traffic to "
            "declare itself; set it to something like "
            "'Evident ingest (+https://github.com/you/evident)'.",
        )
    try:
        # sync worker, so keep it off the event loop
        result = await anyio.to_thread.run_sync(
            lambda: ingest_ticker(req.ticker, form_types=req.form_types,
                                  limit=req.limit))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    return IngestResponse(
        ticker=result.ticker, cik=result.cik, company=result.company,
        chunks_written=result.chunks_written,
        filings=[FilingOut(accession=f.accession, form_type=f.form_type,
                           filed_at=str(f.filed_at), skipped=f.skipped,
                           pages=f.pages, sections=f.sections, chunks=f.chunks,
                           tables=f.tables, error=f.error)
                 for f in result.filings])
