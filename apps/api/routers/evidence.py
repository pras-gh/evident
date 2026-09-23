"""Evidence: resolve a citation to the paragraph it points at.

    GET  /v1/evidence/{chunk_id}      one citation
    POST /v1/evidence/resolve         up to 1,000 citations at once

A citation is a chunk id, optionally narrowed to one of the chunk's paragraphs
and to the entity it is evidence for. Resolving it answers what a reader needs
to check a claim: which filing, which page, which paragraph, where on the page,
in what colour — and how sure extraction was.

Two rules this module exists to get right.

**The page comes from the paragraph.** A chunk can span pages, but the database
stores one `page_number` per chunk, the page it starts on. Resolving to that
would scroll to the wrong page for every paragraph after a page break.
Paragraph ids carry their own page (`27_3` is page 27), so that is what is used.

**A bad citation is reported, not raised**, when resolving several at once. An
answer with five citations, one of them stale, should still show the other four.

Paragraphs are built by `documents.blocks_for`, the same function the page and
reading views use, so a resolved anchor is always on the page it names.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document, Entity, EntityMention
from evident_graph.taxonomy import DEFAULT_HIGHLIGHT, highlight_color
from evident_parser.anchors import paragraph_page

from ..deps import get_db
from ..schemas import (CitationIn, EvidenceEntityOut, EvidenceOut, HighlightOut,
                       ResolvedCitation, ResolveIn, ResolveOut)
from .documents import blocks_for

router = APIRouter(tags=["evidence"])


class CitationError(LookupError):
    """The citation names something that does not exist."""


def _citation_label(form_type: str, page: int | None, section: str | None) -> str:
    where = f"p. {page}" if page else "—"
    return f"{form_type} · {where}" + (f" · {section}" if section else "")


def build_evidence(chunk: Chunk, document: Document, *,
                   paragraph_id: str | None,
                   confidence: float | None,
                   company: Company | None = None,
                   color: str = DEFAULT_HIGHLIGHT,
                   entities: Sequence[EvidenceEntityOut] = ()) -> EvidenceOut:
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
                               bounding_box=b.bounding_box, highlight_color=color)
                  for pg, b in chosen]
    page = highlights[0].page
    return EvidenceOut(
        chunk_id=chunk.id, document_id=document.id,
        ticker=company.ticker if company else None,
        company=company.name if company else None,
        accession=document.accession, form_type=document.form_type,
        fiscal_period=document.fiscal_period,
        filed_at=document.filed_at, source_format=document.source_format,
        page=page, paragraph_id=paragraph_id, paragraph_ids=ids,
        anchors=[h.anchor for h in highlights], section_title=chunk.section_title,
        text=text_, confidence=confidence,
        bounding_box=highlights[0].bounding_box, highlights=highlights,
        highlight_color=color, entities=list(entities),
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
        select(Chunk, Document, Company)
        .join(Document, Document.id == Chunk.document_id)
        .join(Company, Company.id == Document.company_id)
        .where(Chunk.id.in_(ids)))).all()
    by_id = {chunk.id: (chunk, doc, company) for chunk, doc, company in rows}

    mentions: dict[int, list] = defaultdict(list)
    for m in (await db.execute(
            select(EntityMention.chunk_id, EntityMention.paragraph_id,
                   EntityMention.confidence, Entity.slug, Entity.name,
                   Entity.entity_type, Entity.highlight_color)
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


def _entities(rows: list, paragraph_id: str | None) -> list[EvidenceEntityOut]:
    """Entities cited in the paragraph (or anywhere in the chunk), each once,
    at its highest confidence, most confident first."""
    best: dict[str, EvidenceEntityOut] = {}
    for m in rows:
        if paragraph_id is not None and m.paragraph_id != paragraph_id:
            continue
        seen = best.get(m.slug)
        if seen is None or (m.confidence or 0) > (seen.confidence or 0):
            best[m.slug] = EvidenceEntityOut(
                slug=m.slug, name=m.name, entity_type=m.entity_type,
                confidence=m.confidence,
                highlight_color=highlight_color(m.entity_type, m.highlight_color))
    return sorted(best.values(), key=lambda e: (-(e.confidence or 0), e.name.lower()))


def _resolve_one(cit: CitationIn, by_id: dict, mentions: dict[int, list]) -> ResolvedCitation:
    hit = by_id.get(cit.chunk_id)
    if hit is None:
        return ResolvedCitation(index=0, resolved=False, error=f"no chunk {cit.chunk_id}")
    chunk, document, company = hit
    rows = mentions.get(chunk.id, [])
    # Scoped to the cited paragraph and, if named, the entity — otherwise a
    # citation for one claim would borrow another claim's confidence.
    confidence = max(
        (m.confidence for m in rows
         if m.confidence is not None
         and (cit.paragraph_id is None or m.paragraph_id == cit.paragraph_id)
         and (cit.entity is None or m.slug == cit.entity)),
        default=None)
    named = next((m for m in rows if cit.entity and m.slug == cit.entity), None)
    color = (highlight_color(named.entity_type, named.highlight_color) if named
             else DEFAULT_HIGHLIGHT)
    try:
        evidence = build_evidence(chunk, document, paragraph_id=cit.paragraph_id,
                                  confidence=confidence, company=company, color=color,
                                  entities=_entities(rows, cit.paragraph_id))
    except CitationError as exc:
        return ResolvedCitation(index=0, resolved=False, error=str(exc))
    return ResolvedCitation(index=0, resolved=True, evidence=evidence)


@router.get("/evidence/{chunk_id}", response_model=EvidenceOut,
            summary="Resolve one citation")
async def get_evidence(
    chunk_id: int,
    paragraph_id: str | None = Query(None, description="narrow to one paragraph"),
    entity: str | None = Query(None, description="the entity this is evidence for: "
                                                 "scopes confidence and sets the colour"),
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
