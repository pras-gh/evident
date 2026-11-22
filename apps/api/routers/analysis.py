"""AI analysis of filings — paid inference, so every call needs an API key.

    POST /v1/document/{id}/summary

The first customer-facing AI endpoint: attributed to the caller's user and
organization, subject to their limits, cached by filing so a second request
for the same summary costs nothing. Errors from the gateway become the shared
JSON responses registered in `main` (429 usage_limit, 503 ai_unavailable).
"""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_ai.gateway import AIContext
from parallx_ai.summarize import SummaryFailed, summarize_filing
from parallx_db import Chunk, Document

from ..auth import Principal, get_principal
from ..deps import get_db, get_gateway
from ..schemas import FilingSummaryOut, SummaryPointOut
from .documents import unique_blocks

router = APIRouter(tags=["analysis"])


@router.post("/document/{document_id}/summary", response_model=FilingSummaryOut,
             summary="Summarise a filing, with citations",
             responses={429: {"description": "a usage limit was reached"},
                        502: {"description": "the model's answer was unusable"},
                        503: {"description": "no AI provider is available"}})
async def summarize(document_id: int, p: Principal = Depends(get_principal),
                    db: AsyncSession = Depends(get_db), gw=Depends(get_gateway)) -> FilingSummaryOut:
    document = await db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, f"no document {document_id}")
    chunks = (await db.execute(select(Chunk).where(Chunk.document_id == document_id)
                               .order_by(Chunk.ordinal))).scalars().all()
    blocks = [(page, b) for page, b in unique_blocks(chunks) if b.paragraph_id]
    if not blocks:
        raise HTTPException(404, f"no text stored for document {document_id}")

    ctx = AIContext(organization_id=p.organization_id, user_id=p.user_id,
                    source="api:/v1/document/summary")
    paragraphs = [(b.paragraph_id, b.text) for _, b in blocks]
    try:
        result = await anyio.to_thread.run_sync(
            lambda: summarize_filing(accession=document.accession, paragraphs=paragraphs,
                                     ctx=ctx, gw=gw))
    except SummaryFailed as exc:
        raise HTTPException(502, {"error": "ai_bad_response", "message": exc.reason,
                                  "request_id": str(exc.response.request_id)})

    where = {b.paragraph_id: (page, b.chunk_id) for page, b in blocks}
    r = result.response
    return FilingSummaryOut(
        document_id=document.id, accession=document.accession, summary=result.summary,
        key_points=[SummaryPointOut(point=kp.point, citations=[
            {"paragraph_id": pid, "page": where[pid][0], "chunk_id": where[pid][1]}
            for pid in kp.paragraph_ids]) for kp in result.key_points],
        request_id=r.request_id, provider=r.provider, model=r.model, cached=r.cached,
        fallback=r.fallback, cost_usd=r.cost_usd)
