"""Evidence: resolve a citation to the paragraph it points at.

A citation is a chunk id, optionally narrowed to one of the chunk's paragraphs.
Resolving it answers the three things a reader needs to check a claim: which
document, which page, which paragraph — and how sure the extraction was.

Two rules this module exists to get right.

**The page comes from the paragraph.** A chunk can span pages, but the database
stores one `page_number` per chunk, the page it starts on. Resolving to that
would scroll to the wrong page for every paragraph after a page break — in the
benchmark corpus, every chunk spans at least one. Paragraph ids carry their own
page (`27_3` is page 27), so that is what is used.

**A bad citation is reported, not raised**, when resolving several at once. An
answer with five citations, one of them stale, should still show the other four.
"""
from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document, Entity, EntityMention
from evident_parser.anchors import (chunk_anchor, paragraph_page,
                                    split_chunk)

from ..deps import get_db
from ..schemas import (CitationIn, DocumentBlockOut, DocumentPageOut,
                       DocumentPagesOut, EvidenceOut, ResolvedCitation,
                       ResolveIn, ResolveOut)

router = APIRouter(tags=["evidence"])


class CitationError(LookupError):
    """The citation names something that does not exist."""


def _citation_label(form_type: str, page: int | None, section: str | None) -> str:
    where = f"p. {page}" if page else "—"
    return f"{form_type} · {where}" + (f" · {section}" if section else "")


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

    paragraphs = split_chunk(chunk.text, ids, chunk.page_number)

    if paragraphs is None:
        # A table, or text that does not line up with its ids. The reading view
        # renders it as one block, so the citation highlights that block.
        anchors = [chunk_anchor(chunk.id)]
        text = chunk.text
        page = (paragraph_page(paragraph_id, chunk.page_number)
                if paragraph_id else chunk.page_number)
    elif paragraph_id is not None:
        cited = next(p for p in paragraphs if p.paragraph_id == paragraph_id)
        anchors, text, page = [cited.anchor], cited.text, cited.page
    else:
        anchors = [p.anchor for p in paragraphs]
        text = chunk.text
        page = paragraphs[0].page
        paragraph_id = paragraphs[0].paragraph_id

    return EvidenceOut(
        chunk_id=chunk.id, document_id=document.id,
        accession=document.accession, form_type=document.form_type,
        filed_at=document.filed_at, source_format=document.source_format,
        page=page, paragraph_id=paragraph_id, paragraph_ids=ids,
        anchors=anchors, section_title=chunk.section_title, text=text,
        confidence=confidence,
        # No parser records coordinates; see BoundingBox. Null rather than a
        # fabricated rectangle, which would look authoritative and be wrong.
        bounding_box=None,
        citation=_citation_label(document.form_type, page, chunk.section_title),
    )


async def resolve_citations(db: AsyncSession,
                            citations: Sequence[CitationIn]) -> list[ResolvedCitation]:
    """Resolve many citations in two queries, whatever their number.

    Order is preserved and every input gets an output, so the caller can zip
    citations to results by position without bookkeeping.
    """
    ids = {c.chunk_id for c in citations}
    rows = (await db.execute(
        select(Chunk, Document).join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id.in_(ids)))).all()
    by_id = {chunk.id: (chunk, doc) for chunk, doc in rows}

    mentions = (await db.execute(
        select(EntityMention.chunk_id, EntityMention.paragraph_id,
               EntityMention.confidence, Entity.slug)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .where(EntityMention.chunk_id.in_(ids)))).all()

    out: list[ResolvedCitation] = []
    for index, cit in enumerate(citations):
        hit = by_id.get(cit.chunk_id)
        if hit is None:
            out.append(ResolvedCitation(index=index, resolved=False,
                                        error=f"no chunk {cit.chunk_id}"))
            continue
        chunk, document = hit
        # Scoped to the cited paragraph and, if named, the entity — otherwise
        # a citation for one claim would borrow another claim's confidence.
        confidence = max(
            (m.confidence for m in mentions
             if m.chunk_id == chunk.id and m.confidence is not None
             and (cit.paragraph_id is None or m.paragraph_id == cit.paragraph_id)
             and (cit.entity is None or m.slug == cit.entity)),
            default=None)
        try:
            evidence = build_evidence(chunk, document,
                                      paragraph_id=cit.paragraph_id,
                                      confidence=confidence)
        except CitationError as exc:
            out.append(ResolvedCitation(index=index, resolved=False, error=str(exc)))
            continue
        out.append(ResolvedCitation(index=index, resolved=True, evidence=evidence))
    return out


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
             summary="Resolve every citation in an answer")
async def resolve(req: ResolveIn, db: AsyncSession = Depends(get_db)) -> ResolveOut:
    return ResolveOut(citations=await resolve_citations(db, req.citations))


@router.get("/documents/{document_id}/pages", response_model=DocumentPagesOut,
            summary="A filing as a paged reading view")
async def document_pages(document_id: int,
                         db: AsyncSession = Depends(get_db)) -> DocumentPagesOut:
    """Every paragraph of the filing once, in order, on its own page.

    Rebuilt from stored chunks because the raw filing is not stored. Two
    details keep it faithful. Consecutive chunks overlap by a paragraph, so each
    paragraph is emitted the first time it is seen and never again. And a
    paragraph goes on the page its id names, not the page its chunk started on.
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
    seen: set[str] = set()
    for c in chunks:
        paragraphs = split_chunk(c.text, c.paragraph_ids, c.page_number)
        if paragraphs is None:
            pages.setdefault(c.page_number, []).append(DocumentBlockOut(
                anchor=chunk_anchor(c.id),
                kind="table" if not c.paragraph_ids else "paragraph",
                chunk_id=c.id, section_title=c.section_title, text=c.text))
            continue
        for p in paragraphs:
            if p.paragraph_id in seen:
                continue
            seen.add(p.paragraph_id)
            pages.setdefault(p.page, []).append(DocumentBlockOut(
                anchor=p.anchor, kind="paragraph", chunk_id=c.id,
                paragraph_id=p.paragraph_id, section_title=c.section_title,
                text=p.text))

    # Pages ascending, with any unpaged text last rather than first.
    ordered = sorted(pages.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))
    return DocumentPagesOut(
        document_id=document.id, accession=document.accession,
        form_type=document.form_type, filed_at=document.filed_at,
        source_format=document.source_format, ticker=company.ticker,
        page_count=document.page_count,
        pages=[DocumentPageOut(page=page, blocks=blocks) for page, blocks in ordered],
    )
