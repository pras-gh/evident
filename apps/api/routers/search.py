"""Semantic search over chunks."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document

from ..deps import get_db
from ..schemas import SearchHitOut, SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse, summary="Semantic search")
async def search(req: SearchRequest,
                 db: AsyncSession = Depends(get_db)) -> SearchResponse:
    """Nearest chunks, each carrying the citation it came from.

    Retrieval is a supporting index, not the product — so a hit that cannot name
    its document, page and paragraph is not a usable result, and the response
    model makes that non-optional.
    """
    from evident_retrieval.embed import default_embedder

    started = time.perf_counter()
    embedder = default_embedder()
    vector = embedder.embed([req.query]).vectors[0]

    distance = Chunk.embedding.cosine_distance(vector)
    stmt = (select(Chunk, Document, distance.label("distance"))
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.embedding.is_not(None))
            .order_by(distance)
            .limit(req.k))
    if req.ticker:
        stmt = stmt.join(Company, Company.id == Document.company_id).where(
            Company.ticker == req.ticker.upper())
    if req.form_types:
        stmt = stmt.where(Document.form_type.in_(req.form_types))

    rows = (await db.execute(stmt)).all()
    hits = [SearchHitOut(
        chunk_id=c.id, paragraph_id=c.paragraph_id, score=round(1 - float(dist), 6),
        text=c.text, accession=d.accession, form_type=d.form_type,
        filed_at=d.filed_at, page_number=c.page_number,
        section_title=c.section_title, citation=c.citation(),
    ) for c, d, dist in rows]

    return SearchResponse(query=req.query, hits=hits,
                          took_ms=round((time.perf_counter() - started) * 1000, 2),
                          embedder=f"{embedder.provider}/{embedder.model}")
