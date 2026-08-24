"""Company memory, topic and timeline endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import (Chunk, Company, Document, Metric, Person, Risk,
                        TimelineEvent, Topic, TopicMention)

from ..deps import get_company, get_db
from ..schemas import (CompanyMemoryOut, MentionOut, Provenance, RiskOut,
                       TimelineEventOut, TopicDetailOut, TopicOut)

router = APIRouter(prefix="/companies", tags=["memory"])


@router.get("/{ticker}/memory", response_model=CompanyMemoryOut,
            summary="Company memory")
async def company_memory(company: Company = Depends(get_company),
                         db: AsyncSession = Depends(get_db)) -> CompanyMemoryOut:
    async def count(model) -> int:
        return (await db.execute(
            select(func.count()).select_from(model)
            .where(model.company_id == company.id))).scalar_one()

    docs = (await db.execute(
        select(func.count(), func.min(Document.filed_at), func.max(Document.filed_at))
        .where(Document.company_id == company.id))).one()

    top = list((await db.execute(
        select(Topic).where(Topic.company_id == company.id)
        .order_by(Topic.mention_count.desc()).limit(10))).scalars())

    return CompanyMemoryOut(
        company_id=company.id, cik=company.cik, ticker=company.ticker,
        name=company.name, document_count=docs[0],
        earliest_filing=docs[1], latest_filing=docs[2],
        counts={"topics": await count(Topic), "people": await count(Person),
                "risks": await count(Risk), "metrics": await count(Metric),
                "timeline_events": await count(TimelineEvent)},
        top_topics=[TopicOut.model_validate(t, from_attributes=True) for t in top],
    )


@router.get("/{ticker}/topics", response_model=list[TopicOut], summary="Topics")
async def list_topics(company: Company = Depends(get_company),
                      db: AsyncSession = Depends(get_db),
                      limit: int = Query(50, le=500)) -> list[TopicOut]:
    rows = (await db.execute(
        select(Topic).where(Topic.company_id == company.id)
        .order_by(Topic.mention_count.desc()).limit(limit))).scalars()
    return [TopicOut.model_validate(t, from_attributes=True) for t in rows]


@router.get("/{ticker}/topics/{slug}", response_model=TopicDetailOut,
            summary="One topic, with every mention")
async def get_topic(slug: str, company: Company = Depends(get_company),
                    db: AsyncSession = Depends(get_db)) -> TopicDetailOut:
    topic = (await db.execute(
        select(Topic).where(Topic.company_id == company.id, Topic.slug == slug)
    )).scalar_one_or_none()
    if topic is None:
        raise HTTPException(404, f"No topic '{slug}' for {company.ticker}")

    rows = (await db.execute(
        select(TopicMention, Chunk, Document)
        .join(Chunk, Chunk.id == TopicMention.chunk_id)
        .join(Document, Document.id == TopicMention.document_id)
        .where(TopicMention.topic_id == topic.id)
        .order_by(TopicMention.observed_at.desc()))).all()

    return TopicDetailOut(
        **TopicOut.model_validate(topic, from_attributes=True).model_dump(),
        mentions=[MentionOut(
            observed_at=m.observed_at, quote=m.quote, accession=d.accession,
            form_type=d.form_type, section_title=c.section_title,
            # provenance is required on the model, so a mention cannot be
            # serialised without saying where it came from
            provenance=Provenance(
                chunk_hash=c.chunk_hash, document_id=d.id,
                page=m.page_number or c.page_number,
                paragraph_id=m.paragraph_id or (c.paragraph_ids or [None])[0],
                confidence=m.confidence),
        ) for m, c, d in rows],
    )


@router.get("/{ticker}/timeline", response_model=list[TimelineEventOut],
            summary="Timeline")
async def timeline(company: Company = Depends(get_company),
                   db: AsyncSession = Depends(get_db),
                   kind: str | None = None,
                   since: date | None = None,
                   limit: int = Query(100, le=1000)) -> list[TimelineEventOut]:
    stmt = (select(TimelineEvent).where(TimelineEvent.company_id == company.id)
            .order_by(TimelineEvent.occurred_at.desc()).limit(limit))
    if kind:
        stmt = stmt.where(TimelineEvent.kind == kind)
    if since:
        stmt = stmt.where(TimelineEvent.occurred_at >= since)
    rows = (await db.execute(stmt)).scalars()
    return [TimelineEventOut.model_validate(e, from_attributes=True) for e in rows]


@router.get("/{ticker}/risks", response_model=list[RiskOut], summary="Risks")
async def risks(company: Company = Depends(get_company),
                db: AsyncSession = Depends(get_db),
                status: str | None = Query(None, description="active | dropped")
                ) -> list[RiskOut]:
    """A `dropped` risk stopped being disclosed. It is kept, not deleted —
    the disappearance is the finding."""
    stmt = select(Risk).where(Risk.company_id == company.id)
    if status:
        stmt = stmt.where(Risk.status == status)
    rows = (await db.execute(stmt.order_by(Risk.last_seen_at.desc()))).scalars()
    return [RiskOut.model_validate(r, from_attributes=True) for r in rows]
