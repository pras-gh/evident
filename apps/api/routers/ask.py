"""Answers to a question: from the live web, or from the filings.

    POST /v1/ask

The search page calls this after search, passing what search worked out — the
kind of question and the passages it found — so nothing is read or searched
twice. "Why is Nvidia down" is answered from the web (Gemini with Google
Search: paid keys only); "what are Nvidia's China risks" from the passages,
citing them. When the web is unavailable and there are passages, the answer
comes from those, and says so.
"""
from __future__ import annotations

import logging
import time

import anyio
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parallx_db import Chunk, Company, Document

from ..deps import get_db, get_gateway
from ..schemas import AskCitationOut, AskRequest, AskResponse, AskSourceOut

router = APIRouter(tags=["ask"])
log = logging.getLogger("parallx.ask")

#: Passages a filings answer may draw on: enough to cover a topic, few enough
#: that the prompt stays small.
MAX_PASSAGES = 12

BILLING_NOTICE = ("Live market answers need billing enabled on the Gemini API key: "
                  "Google’s web search isn’t available on the free tier.")
#: Once Google has refused web search for want of billing, say so without
#: asking again for this long.
REFUSAL_MEMORY_S = 600.0
_web_refused_at: float | None = None


def _web_refused() -> bool:
    return _web_refused_at is not None and time.monotonic() - _web_refused_at < REFUSAL_MEMORY_S


async def _context():
    from parallx_ai.gateway import AIContext, internal_organization_id
    org = await anyio.to_thread.run_sync(internal_organization_id)
    return AIContext(organization_id=org, source="api:/v1/ask")


def _month(d) -> str:
    return d.strftime("%b %Y")


async def _passages(db: AsyncSession, chunk_ids: list[int]):
    from parallx_ai.answer import Passage
    rows = {c.id: (c, d, co) for c, d, co in (await db.execute(
        select(Chunk, Document, Company)
        .join(Document, Document.id == Chunk.document_id)
        .join(Company, Company.id == Document.company_id)
        .where(Chunk.id.in_(chunk_ids)))).all()}
    ordered = [rows[i] for i in dict.fromkeys(chunk_ids) if i in rows][:MAX_PASSAGES]
    passages = [Passage(text=c.text, label=f"{co.name} {d.form_type}, filed "
                                           f"{_month(d.filed_at)}, {c.citation()}")
                for c, d, co in ordered]
    return ordered, passages


@router.post("/ask", response_model=AskResponse, summary="Answer a question")
async def ask(req: AskRequest, db: AsyncSession = Depends(get_db),
              gw=Depends(get_gateway)) -> AskResponse:
    global _web_refused_at
    from parallx_ai.answer import AnswerIncomplete, answer_from_filings, answer_from_web
    from parallx_ai.gateway import AllProvidersFailed

    ctx = await _context()
    notice: str | None = None

    if req.kind in ("market", "general") or (req.kind is None and not req.chunk_ids):
        if _web_refused():
            notice = BILLING_NOTICE
        else:
            try:
                a = await anyio.to_thread.run_sync(lambda: answer_from_web(req.query, ctx, gw))
            except AllProvidersFailed as exc:
                if "billing" in str(exc).lower():
                    _web_refused_at = time.monotonic()
                    notice = BILLING_NOTICE
                else:
                    notice = "Couldn’t get a live answer right now. Try again in a minute."
            except AnswerIncomplete:
                notice = "The live answer came back incomplete, so it isn’t shown."
            else:
                return AskResponse(
                    mode="web", answer=a.text,
                    sources=[AskSourceOut(**s) for s in a.sources],
                    search_suggestions=a.search_suggestions,
                    model=f"{a.response.provider}:{a.response.model}", cost_usd=a.cost_usd)

    if not req.chunk_ids:
        return AskResponse(mode="none", notice=notice)

    ordered, passages = await _passages(db, req.chunk_ids)
    if not passages:
        return AskResponse(mode="none", notice=notice)
    try:
        a = await anyio.to_thread.run_sync(
            lambda: answer_from_filings(req.query, passages, ctx, gw))
    except (AllProvidersFailed, AnswerIncomplete):
        return AskResponse(mode="none", notice=notice or
                           "Couldn’t write an answer right now. The passages are below.")
    if notice:
        notice += " Here is what the SEC filings say instead."
    citations = [AskCitationOut(n=n, chunk_id=c.id,
                                paragraph_id=(c.paragraph_ids or [None])[0],
                                company=co.name, form_type=d.form_type,
                                filed_at=d.filed_at, citation=c.citation())
                 for n in a.cited for c, d, co in [ordered[n - 1]]]
    return AskResponse(mode="filings", answer=a.text, citations=citations, notice=notice,
                       model=f"{a.response.provider}:{a.response.model}",
                       cost_usd=a.cost_usd)
