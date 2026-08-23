"""Stage orchestration: fetch → parse → chunk → embed → store."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import edgar, store
from .chunker import chunk_document
from .embed import Embedder, default_embedder
from .models import Company, Document, ParsedDocument
from .parse_html import parse_html

_FY = re.compile(r"^(\d{4})-(\d{2})-\d{2}$")


@dataclass(slots=True)
class Result:
    accession: str
    skipped: bool
    document_id: int | None
    stats: dict[str, Any]


def fiscal_period(form_type: str, report_date: str | None) -> str | None:
    """Best-effort label. Returns None rather than guessing a quarter wrong."""
    if not report_date:
        return None
    m = _FY.match(report_date)
    if not m:
        return None
    year, month = m.group(1), int(m.group(2))
    if form_type.startswith("10-K"):
        return f"FY{year}"
    if form_type.startswith("10-Q"):
        return f"Q{(month - 1) // 3 + 1} {year}"
    return year


def ingest_accession(
    *,
    cik: str,
    accession: str,
    dsn: str | None = None,
    embedder: Embedder | None = None,
    target_tokens: int = 350,
) -> Result:
    started = datetime.now(timezone.utc)
    submission = edgar.fetch_submission(cik)
    filing = edgar.find_filing(submission, accession)
    url = edgar.document_url(cik, accession, filing["primary_document"])
    raw, sha = edgar.fetch_document(url)

    conn = store.connect(dsn) if dsn else None
    try:
        if conn and store.already_ingested(conn, filing["accession"], sha):
            store.log_run(conn, accession=filing["accession"], stage="fetch",
                          status="skipped", detail="content unchanged",
                          started_at=started)
            return Result(filing["accession"], True, None, {})

        parsed = _parse(raw, url, filing, cik, sha)
        chunks = chunk_document(
            accession=parsed.document.accession,
            blocks=parsed.blocks,
            tables=parsed.tables,
            sections=parsed.sections,
            target_tokens=target_tokens,
        )
        emb = (embedder or default_embedder()).embed([c.text for c in chunks])

        stats = parsed.stats() | {"chunks": len(chunks),
                                  "embedder": f"{emb.provider}/{emb.model}"}
        if not conn:
            return Result(parsed.document.accession, False, None, stats)

        company = Company(
            cik=cik.zfill(10),
            name=submission.get("name", "Unknown"),
            ticker=(submission.get("tickers") or [None])[0],
            sic=submission.get("sic"),
        )
        document_id = store.write_document(conn, company=company, parsed=parsed,
                                           chunks=chunks, embeddings=emb)
        store.log_run(conn, accession=parsed.document.accession, stage="ingest",
                      status="ok", detail=str(stats), started_at=started)
        return Result(parsed.document.accession, False, document_id, stats)
    finally:
        if conn:
            conn.close()


def _parse(raw: bytes, url: str, filing: dict[str, Any], cik: str, sha: str) -> ParsedDocument:
    is_pdf = url.lower().endswith(".pdf") or raw[:5] == b"%PDF-"
    document = Document(
        accession=filing["accession"],
        cik=cik.zfill(10),
        form_type=filing["form_type"],
        filed_date=filing["filed_date"],
        published_at=filing["published_at"],
        source_url=url,
        source_format="pdf" if is_pdf else "html",
        content_sha256=sha,
        fiscal_period=fiscal_period(filing["form_type"], filing.get("report_date")),
    )
    parsed = ParsedDocument(document=document)
    if is_pdf:
        import tempfile

        from .parse_pdf import parse_pdf
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(raw)
            path = fh.name
        sections, blocks, tables, pages = parse_pdf(path, accession=document.accession)
    else:
        sections, blocks, tables, pages = parse_html(
            raw.decode("utf-8", errors="replace"), accession=document.accession
        )
    parsed.sections, parsed.blocks, parsed.tables = sections, blocks, tables
    document.page_count = pages
    return parsed
