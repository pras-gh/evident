"""Ingestion worker — EDGAR to parsed, chunked rows.

Downloads a filing, parses HTML or PDF, splits it into paragraph-level chunks
carrying page number and section title, and writes them through the V1
repositories.

Idempotent on the content digest: an unchanged filing is recognised and skipped
before it is parsed, so re-running over a company is cheap and safe. An *amended*
filing has different bytes, so it is re-parsed and its chunks replaced wholesale
— patching in place would leave orphaned paragraphs that still answer queries.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import date

from evident_db import session_scope
from evident_db.repositories import (replace_chunks, upsert_company,
                                     upsert_document)
from evident_parser import edgar
from evident_parser.chunker import chunk_document
from evident_parser.html import parse_html
from evident_parser.models import Block, content_id, fiscal_period

log = logging.getLogger("evident.ingest_worker")


@dataclass(slots=True)
class FilingResult:
    accession: str
    form_type: str
    filed_at: date
    skipped: bool = False
    pages: int = 0
    sections: int = 0
    chunks: int = 0
    tables: int = 0
    error: str | None = None


@dataclass(slots=True)
class IngestResult:
    ticker: str
    cik: str
    company: str
    filings: list[FilingResult] = field(default_factory=list)

    @property
    def chunks_written(self) -> int:
        return sum(f.chunks for f in self.filings)



def ingest_ticker(ticker: str, *, form_types: list[str] | None = None,
                  limit: int = 1, url: str | None = None,
                  target_tokens: int = 350) -> IngestResult:
    cik = edgar.cik_for_ticker(ticker)
    submission = edgar.fetch_submission(cik)
    filings = edgar.recent_filings(submission, form_types=form_types, limit=limit)

    result = IngestResult(ticker=ticker.upper(), cik=cik,
                          company=submission.get("name", ticker.upper()))

    with session_scope(url) as db:
        company = upsert_company(db, cik=cik, name=result.company,
                                 ticker=(submission.get("tickers") or [ticker])[0],
                                 sic=submission.get("sic"))
        db.flush()

        for filing in filings:
            try:
                result.filings.append(
                    _ingest_one(db, company_id=company.id, cik=cik, filing=filing,
                                target_tokens=target_tokens))
            except Exception as exc:                      # one bad filing must not
                log.exception("failed on %s", filing["accession"])  # sink the batch
                result.filings.append(FilingResult(
                    accession=filing["accession"], form_type=filing["form_type"],
                    filed_at=filing["filed_date"], error=str(exc)))
    return result


def _ingest_one(db, *, company_id: int, cik: str, filing: dict,
                target_tokens: int) -> FilingResult:
    url = edgar.document_url(cik, filing["accession"], filing["primary_document"])
    raw, sha = edgar.fetch_document(url)
    is_pdf = url.lower().endswith(".pdf") or raw[:5] == b"%PDF-"

    document, is_new = upsert_document(
        db, company_id=company_id, accession=filing["accession"],
        form_type=filing["form_type"], filed_at=filing["filed_date"],
        published_at=filing["published_at"], source_url=url,
        source_format="pdf" if is_pdf else "html", content_sha256=sha,
        fiscal_period=fiscal_period(filing["form_type"], filing.get("report_date")))
    db.flush()

    out = FilingResult(accession=filing["accession"], form_type=filing["form_type"],
                       filed_at=filing["filed_date"])
    if not is_new:
        out.skipped = True
        log.info("%s unchanged — skipped", filing["accession"])
        return out

    sections, blocks, tables, pages = _parse(raw, is_pdf, filing["accession"])
    section_by_ordinal = {s.ordinal: s for s in sections}
    chunks = chunk_document(accession=filing["accession"], blocks=blocks,
                            tables=tables, sections=sections,
                            target_tokens=target_tokens)

    rows = []
    for c in chunks:
        section = section_by_ordinal.get(c.section_ordinal)
        rows.append(dict(
            chunk_key=c.chunk_id, paragraph_ids=c.paragraph_ids or [],
            ordinal=c.ordinal,
            page_number=c.page_start,
            section_title=section.title if section else None,
            section_path=section.path if section else None,
            text=c.text, char_count=len(c.text), token_estimate=c.token_estimate))
    out.chunks = replace_chunks(db, document_id=document.id, chunks=rows)
    document.page_count = pages
    out.pages, out.sections, out.tables = pages, len(sections), len(tables)
    log.info("%s %s — %d pages, %d sections, %d chunks",
             filing["form_type"], filing["accession"], pages, len(sections), out.chunks)
    return out


def _parse(raw: bytes, is_pdf: bool, accession: str):
    if not is_pdf:
        return parse_html(raw.decode("utf-8", errors="replace"), accession=accession)
    from evident_parser.pdf import parse_pdf
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(raw)
        path = fh.name
    return parse_pdf(path, accession=accession)


if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="evident-ingest")
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--forms", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=1)
    args = ap.parse_args()

    r = ingest_ticker(args.ticker, form_types=args.forms, limit=args.limit)
    print(json.dumps({"ticker": r.ticker, "cik": r.cik, "company": r.company,
                      "chunks": r.chunks_written,
                      "filings": [f.__dict__ for f in r.filings]},
                     indent=2, default=str))
