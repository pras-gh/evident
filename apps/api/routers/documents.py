"""Documents: a company's filings, and each filing as pages.

    GET /v1/company/{ticker}/documents            a company's filings
    GET /v1/document/{id}                         a filing and its pages, for the viewer
    GET /v1/document/{id}/page/{page}             one page: paragraphs, anchors, boxes
    GET /v1/document/{id}/page/{page}/image       the page as rendered (?size=thumb)
    GET /v1/document/{id}/pdf                     a PDF whose page N is page N
    GET /v1/documents/{id}/pages                  the whole filing as a reading view

Every paragraph anywhere in the API is built by `blocks_for`, here, so an
anchor a citation resolves to is always a block on the page it names — in the
reading view, on the page API, and under the box drawn over the page image.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document, DocumentPage, store
from evident_parser.anchors import chunk_anchor, paragraph_page, split_chunk

from ..deps import get_company, get_db
from ..schemas import (BoundingBox, CompanyDocumentsOut, DocumentBlockOut,
                       DocumentDetailOut, DocumentPageOut, DocumentPagesOut,
                       DocumentSummaryOut, PageOut, PageSummaryOut)
from .timeline import filing_url

router = APIRouter(tags=["documents"])

#: Versioned URLs never change content, so they can be cached for good.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _box(chunk: Chunk, paragraph_id: str | None) -> BoundingBox | None:
    raw = (chunk.paragraph_boxes or {}).get(paragraph_id) if paragraph_id else None
    return BoundingBox(**raw) if raw else None


def blocks_for(chunk: Chunk) -> list[tuple[int | None, DocumentBlockOut]]:
    """A chunk as the blocks a reader sees, each with its page.

    Prose splits into its paragraphs, each on the page its id names and with
    its box. A table, or text that does not line up with its ids, is one block
    anchored by the chunk.
    """
    paragraphs = split_chunk(chunk.text, chunk.paragraph_ids, chunk.page_number)
    if paragraphs is None:
        return [(chunk.page_number, DocumentBlockOut(
            anchor=chunk_anchor(chunk.id),
            kind="table" if not chunk.paragraph_ids else "paragraph",
            chunk_id=chunk.id, section_title=chunk.section_title, text=chunk.text))]
    return [(p.page, DocumentBlockOut(
        anchor=p.anchor, kind="paragraph", chunk_id=chunk.id,
        paragraph_id=p.paragraph_id, section_title=chunk.section_title,
        text=p.text, bounding_box=_box(chunk, p.paragraph_id)))
        for p in paragraphs]


def unique_blocks(chunks: Iterable[Chunk]) -> Iterable[tuple[int | None, DocumentBlockOut]]:
    """Every block once, in order. Consecutive chunks overlap by a paragraph,
    so each paragraph is emitted the first time it is seen and never again."""
    seen: set[str] = set()
    for c in chunks:
        for page, block in blocks_for(c):
            if block.anchor in seen:
                continue
            seen.add(block.anchor)
            yield page, block


def _pages_with_text(rows: Iterable[tuple[list[str] | None, int | None]]) -> set[int]:
    pages: set[int] = set()
    for paragraph_ids, page_number in rows:
        if paragraph_ids:
            pages.update(p for p in (paragraph_page(pid, page_number) for pid in paragraph_ids)
                         if p)
        elif page_number:
            pages.add(page_number)
    return pages


def _version(document: Document) -> str:
    return str(int(document.rendered_at.timestamp())) if document.rendered_at else "0"


def page_image_url(document: Document, page: int, *, thumbnail: bool = False) -> str:
    size = "&size=thumb" if thumbnail else ""
    return f"/v1/document/{document.id}/page/{page}/image?v={_version(document)}{size}"


async def _document(db: AsyncSession, document_id: int) -> tuple[Document, Company]:
    row = (await db.execute(
        select(Document, Company).join(Company, Company.id == Document.company_id)
        .where(Document.id == document_id))).first()
    if row is None:
        raise HTTPException(404, f"no document {document_id}")
    return row[0], row[1]


# ---------------------------------------------------------------- endpoints

@router.get("/company/{ticker}/documents", response_model=CompanyDocumentsOut,
            summary="A company's filings")
async def company_documents(
    company: Company = Depends(get_company),
    db: AsyncSession = Depends(get_db),
    form_type: str | None = Query(None, description="e.g. 10-K"),
    limit: int = Query(100, ge=1, le=500),
) -> CompanyDocumentsOut:
    stmt = (select(Document).where(Document.company_id == company.id)
            .order_by(Document.filed_at.desc(), Document.id.desc()).limit(limit))
    if form_type:
        stmt = stmt.where(Document.form_type == form_type.upper())
    documents = (await db.execute(stmt)).scalars().all()

    rows: dict[int, list] = defaultdict(list)
    if documents:
        for r in (await db.execute(
                select(Chunk.document_id, Chunk.paragraph_ids, Chunk.page_number,
                       # an object, not merely not-NULL: a JSON `null` is not NULL
                       (func.jsonb_typeof(Chunk.paragraph_boxes) == "object").label("boxed"))
                .where(Chunk.document_id.in_([d.id for d in documents])))).all():
            rows[r.document_id].append(r)

    out = []
    for d in documents:
        pages = _pages_with_text((r.paragraph_ids, r.page_number) for r in rows[d.id])
        paragraphs = {pid.split("#")[0] for r in rows[d.id] for pid in (r.paragraph_ids or [])}
        out.append(DocumentSummaryOut(
            document_id=d.id, accession=d.accession, form_type=d.form_type,
            fiscal_period=d.fiscal_period, filed_at=d.filed_at,
            published_at=d.published_at, source_format=d.source_format,
            page_count=d.page_count, pages_with_text=len(pages),
            first_page=min(pages) if pages else None, paragraph_count=len(paragraphs),
            has_bounding_boxes=any(r.boxed for r in rows[d.id]),
            url=filing_url(company.cik, d.accession)))
    return CompanyDocumentsOut(ticker=company.ticker, company=company.name, documents=out)


@router.get("/document/{document_id}", response_model=DocumentDetailOut,
            summary="A filing and its pages, for the viewer")
async def document_detail(document_id: int,
                          db: AsyncSession = Depends(get_db)) -> DocumentDetailOut:
    document, company = await _document(db, document_id)
    rows = {p.page: p for p in (await db.execute(
        select(DocumentPage).where(DocumentPage.document_id == document_id))).scalars()}
    total = max([document.page_count or 0, *rows])
    pages = []
    for n in range(1, total + 1):
        row = rows.get(n)
        pages.append(PageSummaryOut(
            page=n,
            width=row.width if row else 612.0, height=row.height if row else 792.0,
            image_url=page_image_url(document, n) if row and row.image_path else None,
            thumbnail_url=(page_image_url(document, n, thumbnail=True)
                           if row and row.thumbnail_path else None),
            paragraph_count=row.paragraph_count if row else 0))
    return DocumentDetailOut(
        document_id=document.id, ticker=company.ticker, company=company.name,
        accession=document.accession, form_type=document.form_type,
        fiscal_period=document.fiscal_period, filed_at=document.filed_at,
        source_format=document.source_format, page_count=document.page_count,
        rendered_at=document.rendered_at,
        pdf_url=(f"/v1/document/{document.id}/pdf?v={_version(document)}"
                 if document.pdf_path else None),
        url=filing_url(company.cik, document.accession), pages=pages)


#: Chunks with a paragraph on the page — by the page in the paragraph id, or,
#: for tables and ids that carry no page, by the page the chunk starts on.
#: Parenthesised because it is ANDed with the document filter, and a bare OR
#: would bind looser than the AND and match the page in every document.
_ON_PAGE = text(
    "(chunks.page_number = :page OR EXISTS (SELECT 1 FROM unnest(chunks.paragraph_ids) pid "
    "WHERE split_part(split_part(pid, '#', 1), '_', 1) = :page_s))")


@router.get("/document/{document_id}/page/{page}", response_model=PageOut,
            summary="One page of a filing, with anchors and boxes")
async def document_page(document_id: int, page: int,
                        db: AsyncSession = Depends(get_db)) -> PageOut:
    document, company = await _document(db, document_id)
    if page < 1 or (document.page_count and page > document.page_count):
        raise HTTPException(404, f"{document.form_type} {document.accession} has pages "
                                 f"1–{document.page_count or '?'}; no page {page}")

    chunks = (await db.execute(
        select(Chunk).where(Chunk.document_id == document_id,
                            _ON_PAGE.bindparams(page=page, page_s=str(page)))
        .order_by(Chunk.ordinal))).scalars().all()
    blocks = [b for pg, b in unique_blocks(chunks) if pg == page]

    pages = sorted(_pages_with_text((await db.execute(
        select(Chunk.paragraph_ids, Chunk.page_number)
        .where(Chunk.document_id == document_id))).all()))
    row = (await db.execute(select(DocumentPage).where(
        DocumentPage.document_id == document_id, DocumentPage.page == page))).scalar_one_or_none()
    boxed = next((b.bounding_box for b in blocks if b.bounding_box), None)
    return PageOut(
        document_id=document.id, accession=document.accession,
        form_type=document.form_type, filed_at=document.filed_at,
        ticker=company.ticker, source_format=document.source_format,
        page=page, page_count=document.page_count,
        page_width=row.width if row else (boxed.page_width if boxed else None),
        page_height=row.height if row else (boxed.page_height if boxed else None),
        prev_page=max((p for p in pages if p < page), default=None),
        next_page=min((p for p in pages if p > page), default=None),
        image_url=page_image_url(document, page) if row and row.image_path else None,
        thumbnail_url=(page_image_url(document, page, thumbnail=True)
                       if row and row.thumbnail_path else None),
        blocks=blocks)


def _file(relative: str | None, media_type: str, **kw) -> FileResponse:
    if not relative:
        raise HTTPException(404, "not rendered — run the render worker")
    try:
        path = store.resolve(relative)
    except ValueError:
        raise HTTPException(404, "not found")
    if not path.is_file():
        raise HTTPException(404, "the rendered file is missing — render the filing again")
    return FileResponse(path, media_type=media_type, headers=IMMUTABLE, **kw)


@router.get("/document/{document_id}/page/{page}/image", summary="A page as rendered",
            response_class=FileResponse)
async def page_image(document_id: int, page: int,
                     size: str = Query("full", pattern="^(full|thumb)$"),
                     db: AsyncSession = Depends(get_db)) -> FileResponse:
    row = (await db.execute(select(DocumentPage).where(
        DocumentPage.document_id == document_id, DocumentPage.page == page))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, f"no rendered page {page} for document {document_id}")
    return _file(row.thumbnail_path if size == "thumb" else row.image_path, "image/webp")


@router.get("/document/{document_id}/pdf", summary="The filing as a PDF",
            response_class=FileResponse)
async def document_pdf(document_id: int, db: AsyncSession = Depends(get_db)) -> FileResponse:
    document, _ = await _document(db, document_id)
    return _file(document.pdf_path, "application/pdf",
                 filename=f"{document.accession}.pdf", content_disposition_type="inline")


@router.get("/documents/{document_id}/pages", response_model=DocumentPagesOut,
            summary="A filing as a paged reading view")
async def document_pages(document_id: int,
                         db: AsyncSession = Depends(get_db)) -> DocumentPagesOut:
    """Every paragraph of the filing once, in order, on its own page. The text
    fallback for filings that have not been rendered."""
    document, company = await _document(db, document_id)
    chunks = (await db.execute(
        select(Chunk).where(Chunk.document_id == document_id)
        .order_by(Chunk.ordinal))).scalars().all()

    pages: dict[int | None, list[DocumentBlockOut]] = {}
    for page, block in unique_blocks(chunks):
        pages.setdefault(page, []).append(block)

    # Pages ascending, with any unpaged text last rather than first.
    ordered = sorted(pages.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    return DocumentPagesOut(
        document_id=document.id, accession=document.accession,
        form_type=document.form_type, filed_at=document.filed_at,
        source_format=document.source_format, ticker=company.ticker,
        page_count=document.page_count,
        pages=[DocumentPageOut(page=page, blocks=blocks) for page, blocks in ordered],
    )
