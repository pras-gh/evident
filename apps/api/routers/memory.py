"""Company memory, entity and timeline endpoints."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import (Chunk, Company, Document, Entity, EntityMention,
                        TimelineEvent)

from ..deps import get_company, get_db
from ..schemas import (CompanyMemoryOut, EntityDetailOut, EntityOut, MentionOut,
                       Provenance, TimelineEventOut)

router = APIRouter(prefix="/companies", tags=["memory"])


@router.get("/{ticker}/memory", response_model=CompanyMemoryOut,
            summary="Company memory")
async def company_memory(company: Company = Depends(get_company),
                         db: AsyncSession = Depends(get_db)) -> CompanyMemoryOut:
    docs = (await db.execute(
        select(func.count(), func.min(Document.filed_at), func.max(Document.filed_at))
        .where(Document.company_id == company.id))).one()

    # one grouped read instead of a count per type
    by_kind = dict((await db.execute(
        select(Entity.entity_type, func.count())
        .where(Entity.company_id == company.id).group_by(Entity.entity_type))).all())

    top = list((await db.execute(
        select(Entity).where(Entity.company_id == company.id)
        .order_by(Entity.mention_count.desc()).limit(10))).scalars())

    return CompanyMemoryOut(
        company_id=company.id, cik=company.cik, ticker=company.ticker,
        name=company.name, document_count=docs[0],
        earliest_filing=docs[1], latest_filing=docs[2],
        counts={k: int(v) for k, v in by_kind.items()},
        top_entities=[EntityOut.model_validate(e, from_attributes=True) for e in top],
    )


@router.get("/{ticker}/entities", response_model=list[EntityOut],
            summary="Entities")
async def list_entities(company: Company = Depends(get_company),
                        db: AsyncSession = Depends(get_db),
                        entity_type: str | None = Query(None),
                        status: str | None = Query(
                            None, description="active | dropped — 'dropped' means "
                                              "it stopped being disclosed"),
                        limit: int = Query(50, le=500)) -> list[EntityOut]:
    stmt = select(Entity).where(Entity.company_id == company.id)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    if status:
        stmt = stmt.where(Entity.status == status)
    rows = (await db.execute(
        stmt.order_by(Entity.mention_count.desc()).limit(limit))).scalars()
    return [EntityOut.model_validate(e, from_attributes=True) for e in rows]


@router.get("/{ticker}/entities/{slug}", response_model=EntityDetailOut,
            summary="One entity, with every mention")
async def get_entity(slug: str, company: Company = Depends(get_company),
                     db: AsyncSession = Depends(get_db),
                     entity_type: str | None = Query(None)) -> EntityDetailOut:
    stmt = select(Entity).where(Entity.company_id == company.id,
                                Entity.slug == slug)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    entity = (await db.execute(stmt)).scalars().first()
    if entity is None:
        raise HTTPException(404, f"No entity '{slug}' for {company.ticker}")

    rows = (await db.execute(
        select(EntityMention, Chunk, Document)
        .outerjoin(Chunk, Chunk.id == EntityMention.chunk_id)
        .join(Document, Document.id == EntityMention.document_id)
        .where(EntityMention.entity_id == entity.id)
        .order_by(EntityMention.observed_at.desc()))).all()

    return EntityDetailOut(
        **EntityOut.model_validate(entity, from_attributes=True).model_dump(),
        mentions=[MentionOut(
            observed_at=m.observed_at, quote=m.quote, accession=d.accession,
            form_type=d.form_type,
            section_title=c.section_title if c else None,
            provenance=Provenance(
                chunk_hash=m.chunk_hash or (c.chunk_hash if c else None),
                document_id=d.id,
                page=m.page or (c.page_number if c else None),
                paragraph_id=m.paragraph_id,
                confidence=m.confidence),
        ) for m, c, d in rows],
    )


@router.get("/{ticker}/timeline", response_model=list[TimelineEventOut],
            summary="Timeline")
async def timeline(company: Company = Depends(get_company),
                   db: AsyncSession = Depends(get_db),
                   kind: str | None = None, since: date | None = None,
                   limit: int = Query(100, le=1000)) -> list[TimelineEventOut]:
    stmt = (select(TimelineEvent).where(TimelineEvent.company_id == company.id)
            .order_by(TimelineEvent.occurred_at.desc()).limit(limit))
    if kind:
        stmt = stmt.where(TimelineEvent.kind == kind)
    if since:
        stmt = stmt.where(TimelineEvent.occurred_at >= since)
    rows = (await db.execute(stmt)).scalars()
    return [TimelineEventOut.model_validate(e, from_attributes=True) for e in rows]
