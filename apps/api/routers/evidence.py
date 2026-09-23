"""Evidence: resolve a citation to the paragraph it points at, and serve the
filing it sits in, page by page.

    GET  /v1/evidence/{chunk_id}               one citation
    POST /v1/evidence/resolve                  up to 1,000 citations at once
    GET  /v1/company/{ticker}/documents        a company's filings
    GET  /v1/document/{id}/page/{page}         one page: paragraphs, anchors, boxes
    GET  /v1/documents/{id}/pages              the whole filing as a reading view

A citation is a chunk id, optionally narrowed to one of the chunk's paragraphs.
Resolving it answers what a reader needs to check a claim: which document,
which page, which paragraph, where on the page — and how sure extraction was.

Three rules this module exists to get right.

**The page comes from the paragraph.** A chunk can span pages, but the database
stores one `page_number` per chunk, the page it starts on. Resolving to that
would scroll to the wrong page for every paragraph after a page break.
Paragraph ids carry their own page (`27_3` is page 27), so that is what is used.

**Every block is built one way.** The evidence builder, the page view and the
full reading view all turn chunks into paragraphs through `blocks_for`, so an
anchor a citation resolves to is always an anchor the page actually renders.

**A bad citation is reported, not raised**, when resolving several at once. An
answer with five citations, one of them stale, should still show the other four.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document, Entity, EntityMention
from evident_parser.anchors import chunk_anchor, paragraph_page, split_chunk

from ..deps import get_company, get_db
from ..schemas import (BoundingBox, CitationIn, CompanyDocumentsOut,
                       DocumentBlockOut, DocumentPageOut, DocumentPagesOut,
                       DocumentSummaryOut, EvidenceOut, HighlightOut, PageOut,
                       ResolvedCitation, ResolveIn, ResolveOut)
from .timeline import filing_url

router = APIRouter(tags=["evidence"])


class CitationError(LookupError):
    """The citation names something that does not exist."""


def _citation_label(form_type: str, page: int | None, section: str | None) -> str:
    where = f"p. {page}" if page else "—"
    return f"{form_type} · {where}" + (f" · {section}" if section else "")


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


def build_evidence(chunk: Chunk, document: Document, *,
                   paragraph_id: str | None,
                   confidence: float | None) -> EvidenceOut:
    """One chunk plus an optional paragraph, as a resolved citation.

    Naming a paragraph highlights that paragraph. Naming only the chunk
    highlights every paragraph in it and scrolls to the first — the citation
    said "this passage", so the whole passage is what the reader should see.
    """
    # A table chunk has no paragraph ids, so extraction cites it by its hash.
    # That is a reference to the whole chunk, not to a missing paragraph.
    if paragraph_id is not None and paragraph_id == chunk.chunk_hash:
        paragraph_id = None

    ids = list(chunk.paragraph_ids or [])
    if paragraph_id is not None and paragraph_id not in ids:
        raise CitationError(
            f"paragraph {paragraph_id!r} is not part of chunk {chunk.id}")

    blocks = blocks_for(chunk)
    if blocks and blocks[0][1].paragraph_id is None:
        # A table, or text that does not line up with its ids: one block.
        page, block = blocks[0]
        if paragraph_id:
            page = paragraph_page(paragraph_id, chunk.page_number)
        chosen, text_ = [(page, block)], chunk.text
    elif paragraph_id is not None:
        chosen = [(pg, b) for pg, b in blocks if b.paragraph_id == paragraph_id]
        text_ = chosen[0][1].text
    else:
        chosen, text_ = blocks, chunk.text
        paragraph_id = blocks[0][1].paragraph_id

    highlights = [HighlightOut(anchor=b.anchor, paragraph_id=b.paragraph_id, page=pg,
                               bounding_box=b.bounding_box) for pg, b in chosen]
    page = highlights[0].page
    return EvidenceOut(
        chunk_id=chunk.id, document_id=document.id,
        accession=document.accession, form_type=document.form_type,
        filed_at=document.filed_at, source_format=document.source_format,
        page=page, paragraph_id=paragraph_id, paragraph_ids=ids,
        anchors=[h.anchor for h in highlights], section_title=chunk.section_title,
        text=text_, confidence=confidence,
        bounding_box=highlights[0].bounding_box, highlights=highlights,
        citation=_citation_label(document.form_type, page, chunk.section_title),
    )


async def resolve_citations(db: AsyncSession,
                            citations: Sequence[CitationIn]) -> list[ResolvedCitation]:
    """Resolve many citations in two queries, whatever their number.

    Order is preserved and every input gets an output, so the caller can zip
    citations to results by position without bookkeeping. A citation repeated
    in the input is resolved once: an answer that cites the same paragraph
    forty times costs one resolution, not forty.
    """
    ids = {c.chunk_id for c in citations}
    rows = (await db.execute(
        select(Chunk, Document).join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(ids)))).all()
    by_id = {chunk.id: (chunk, doc) for chunk, doc in rows}

    mentions: dict[int, list] = defaultdict(list)
    for m in (await db.execute(
            select(EntityMention.chunk_id, EntityMention.paragraph_id,
                   EntityMention.confidence, Entity.slug)
            .join(Entity, Entity.id == EntityMention.entity_id)
            .where(EntityMention.chunk_id.in_(ids)))).all():
        mentions[m.chunk_id].append(m)

    memo: dict[tuple, ResolvedCitation] = {}
    out: list[ResolvedCitation] = []
    for index, cit in enumerate(citations):
        key = (cit.chunk_id, cit.paragraph_id, cit.entity)
        if key not in memo:
            memo[key] = _resolve_one(cit, by_id, mentions)
        out.append(memo[key].model_copy(update={"index": index}))
    return out


def _resolve_one(cit: CitationIn, by_id: dict, mentions: dict[int, list]) -> ResolvedCitation:
    hit = by_id.get(cit.chunk_id)
    if hit is None:
        return ResolvedCitation(index=0, resolved=False, error=f"no chunk {cit.chunk_id}")
    chunk, document = hit
    # Scoped to the cited paragraph and, if named, the entity — otherwise a
    # citation for one claim would borrow another claim's confidence.
    confidence = max(
        (m.confidence for m in mentions.get(chunk.id, ())
         if m.confidence is not None
         and (cit.paragraph_id is None or m.paragraph_id == cit.paragraph_id)
         and (cit.entity is None or m.slug == cit.entity)),
        default=None)
    try:
        evidence = build_evidence(chunk, document, paragraph_id=cit.paragraph_id,
                                  confidence=confidence)
    except CitationError as exc:
        return ResolvedCitation(index=0, resolved=False, error=str(exc))
    return ResolvedCitation(index=0, resolved=True, evidence=evidence)


@router.get("/evidence/{chunk_id}", response_model=EvidenceOut,
            summary="Resolve one citation")
async def get_evidence(
    chunk_id: int,
    paragraph_id: str | None = Query(None, description="narrow to one paragraph"),
    entity: str | None = Query(None, description="scope confidence to this entity"),
    db: AsyncSession = Depends(get_db),
) -> EvidenceOut:
    [result] = await resolve_citations(
        db, [CitationIn(chunk_id=chunk_id, paragraph_id=paragraph_id, entity=entity)])
    if not result.resolved:
        raise HTTPException(404, result.error)
    return result.evidence


@router.post("/evidence/resolve", response_model=ResolveOut,
             summary="Resolve every citation in an answer (up to 1,000)")
async def resolve(req: ResolveIn, db: AsyncSession = Depends(get_db)) -> ResolveOut:
    return ResolveOut(citations=await resolve_citations(db, req.citations))


# ------------------------------------------------------------------ documents

def _unique_blocks(chunks: Iterable[Chunk]) -> Iterable[tuple[int | None, DocumentBlockOut]]:
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
    row = (await db.execute(
        select(Document, Company).join(Company, Company.id == Document.company_id)
        .where(Document.id == document_id))).first()
    if row is None:
        raise HTTPException(404, f"no document {document_id}")
    document, company = row
    if page < 1 or (document.page_count and page > document.page_count):
        raise HTTPException(404, f"{document.form_type} {document.accession} has pages "
                                 f"1–{document.page_count or '?'}; no page {page}")

    chunks = (await db.execute(
        select(Chunk).where(Chunk.document_id == document_id,
                            _ON_PAGE.bindparams(page=page, page_s=str(page)))
        .order_by(Chunk.ordinal))).scalars().all()
    blocks = [b for pg, b in _unique_blocks(chunks) if pg == page]

    pages = sorted(_pages_with_text((await db.execute(
        select(Chunk.paragraph_ids, Chunk.page_number)
        .where(Chunk.document_id == document_id))).all()))
    boxed = next((b.bounding_box for b in blocks if b.bounding_box), None)
    return PageOut(
        document_id=document.id, accession=document.accession,
        form_type=document.form_type, filed_at=document.filed_at,
        ticker=company.ticker, source_format=document.source_format,
        page=page, page_count=document.page_count,
        page_width=boxed.page_width if boxed else None,
        page_height=boxed.page_height if boxed else None,
        prev_page=max((p for p in pages if p < page), default=None),
        next_page=min((p for p in pages if p > page), default=None),
        blocks=blocks)


@router.get("/documents/{document_id}/pages", response_model=DocumentPagesOut,
            summary="A filing as a paged reading view")
async def document_pages(document_id: int,
                         db: AsyncSession = Depends(get_db)) -> DocumentPagesOut:
    """Every paragraph of the filing once, in order, on its own page.

    Rebuilt from stored chunks because the raw filing is not stored.
    """
    row = (await db.execute(
        select(Document, Company).join(Company, Company.id == Document.company_id)
        .where(Document.id == document_id))).first()
    if row is None:
        raise HTTPException(404, f"no document {document_id}")
    document, company = row

    chunks = (await db.execute(
        select(Chunk).where(Chunk.document_id == document_id)
        .order_by(Chunk.ordinal))).scalars().all()

    pages: dict[int | None, list[DocumentBlockOut]] = {}
    for page, block in _unique_blocks(chunks):
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
