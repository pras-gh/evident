"""Company timeline: what changed between filings, and where it says so.

    GET /v1/company/{ticker}/timeline

Computed on read from `documents` and `entity_mentions` by
`evident_graph.timeline` — no stored copy to drift, and a re-extracted filing
changes the timeline the moment it lands. Not to be confused with
`/v1/companies/{ticker}/timeline`, the raw `timeline_events` log.

Every change carries the paragraph that shows it, with the chunk id and
paragraph id the evidence viewer resolves.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document, Entity, EntityMention
from evident_graph import timeline as engine
from evident_parser.anchors import paragraph_page, split_chunk

from ..deps import get_company, get_db
from ..schemas import (CompanyTimelineOut, TimelineEntryOut, TimelineEvidenceOut,
                       TimelineFilingOut, TimelineFilingRef, TimelineKind,
                       TimelineThresholds, TimelineTopicOut)

router = APIRouter(prefix="/company", tags=["timeline"])

#: Always sec.gov, whatever origin a filing was ingested from — a link built
#: from the stored source URL would point at a fixture server in the demo.
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def filing_url(cik: str, accession: str) -> str:
    return f"{EDGAR_ARCHIVES}/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


async def load_timeline(db: AsyncSession, company: Company) -> engine.Timeline:
    documents = (await db.execute(
        select(Document).where(Document.company_id == company.id))).scalars().all()
    filings = [engine.Filing(document_id=d.id, accession=d.accession,
                             form_type=d.form_type, filed_at=d.filed_at,
                             fiscal_period=d.fiscal_period, page_count=d.page_count)
               for d in documents]

    topics = [engine.Topic(entity_id=e.id, slug=e.slug, name=e.name,
                           entity_type=e.entity_type)
              for e in (await db.execute(
                  select(Entity).where(Entity.company_id == company.id))).scalars()]

    rows = (await db.execute(
        select(EntityMention.id, EntityMention.entity_id, EntityMention.document_id,
               EntityMention.chunk_id, EntityMention.paragraph_id, EntityMention.page,
               EntityMention.quote, EntityMention.confidence)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .where(Entity.company_id == company.id))).all()

    # Paragraph text, so evidence for an expansion can be a paragraph that is
    # new this year rather than one carried over unchanged. Only the chunks
    # something cites are read.
    chunk_ids = {r.chunk_id for r in rows if r.chunk_id is not None}
    texts: dict[tuple[int, str | None], str] = {}
    if chunk_ids:
        for c in (await db.execute(
                select(Chunk.id, Chunk.text, Chunk.paragraph_ids, Chunk.page_number)
                .where(Chunk.id.in_(chunk_ids)))).all():
            paragraphs = split_chunk(c.text, c.paragraph_ids, c.page_number)
            if paragraphs is None:
                texts[(c.id, None)] = c.text
            else:
                texts.update(((c.id, p.paragraph_id), p.text) for p in paragraphs)

    mentions = [engine.Mention(
        entity_id=r.entity_id, document_id=r.document_id, chunk_id=r.chunk_id,
        paragraph_id=r.paragraph_id,
        # the paragraph's own page, as the evidence resolver reports it
        page=paragraph_page(r.paragraph_id, r.page),
        quote=r.quote, confidence=r.confidence, mention_id=r.id,
        text=(texts.get((r.chunk_id, r.paragraph_id)) or texts.get((r.chunk_id, None))
              if r.chunk_id is not None else None),
    ) for r in rows]

    return engine.build_timeline(filings, topics, mentions)


def _ref(f: engine.Filing | None) -> TimelineFilingRef | None:
    if f is None:
        return None
    return TimelineFilingRef(document_id=f.document_id, accession=f.accession,
                             form_type=f.form_type, fiscal_period=f.fiscal_period,
                             filed_at=f.filed_at)


def entry(e: engine.Event, forms: dict[int, str]) -> TimelineEntryOut:
    ev = e.evidence
    return TimelineEntryOut(
        id=e.key, date=e.date, date_label=f"{e.date:%b %Y}", kind=e.kind,
        category=e.category, title=e.title, summary=e.summary,
        topic=(TimelineTopicOut(slug=e.topic.slug, name=e.topic.name,
                                entity_type=e.topic.entity_type) if e.topic else None),
        filing=_ref(e.filing), compared_with=_ref(e.compared_with),
        paragraphs=e.paragraphs, previous_paragraphs=e.previous_paragraphs,
        evidence=None if ev is None else TimelineEvidenceOut(
            chunk_id=ev.chunk_id, paragraph_id=ev.paragraph_id,
            document_id=ev.document_id, page=ev.page, quote=ev.quote,
            confidence=ev.confidence, new_paragraph=ev.new_paragraph,
            citation=(f"{forms.get(ev.document_id, '?')} · "
                      f"{f'p. {ev.page}' if ev.page else 'page unknown'}")),
    )


@router.get("/{ticker}/timeline", response_model=CompanyTimelineOut,
            summary="What changed between filings")
async def company_timeline(
    company: Company = Depends(get_company),
    db: AsyncSession = Depends(get_db),
    category: str | None = Query(None, description="Risk, Product, Geography, …, "
                                                   "or Filing; case-insensitive"),
    kind: TimelineKind | None = Query(None),
    entity: str | None = Query(None, description="one entity, by slug"),
    since: date | None = Query(None, description="events on or after this date"),
    limit: int = Query(100, ge=1, le=500),
) -> CompanyTimelineOut:
    timeline = await load_timeline(db, company)

    # Filters apply after the build: `since` must not change what a filing was
    # compared with, or the first filing after it would become a baseline.
    events = [e for e in timeline.events
              if (kind is None or e.kind == kind)
              and (entity is None or (e.topic and e.topic.slug == entity))
              and (since is None or e.date >= since)]
    categories: dict[str, int] = {}
    for e in events:
        categories[e.category] = categories.get(e.category, 0) + 1
    if category:
        events = [e for e in events if e.category.lower() == category.lower()]

    forms = {f.document_id: f.form_type for f in timeline.filings}
    return CompanyTimelineOut(
        ticker=company.ticker, company=company.name,
        filings=[TimelineFilingOut(
            document_id=f.document_id, accession=f.accession, form_type=f.form_type,
            fiscal_period=f.fiscal_period, filed_at=f.filed_at, page_count=f.page_count,
            coverage=timeline.coverage.get(f.accession, "not compared"),
            url=filing_url(company.cik, f.accession),
        ) for f in reversed(timeline.filings)],
        thresholds=TimelineThresholds(min_delta=engine.MIN_DELTA,
                                      min_ratio=engine.MIN_RATIO),
        categories=categories, total=len(events),
        events=[entry(e, forms) for e in events[:limit]],
    )
