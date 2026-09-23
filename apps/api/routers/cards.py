"""Memory cards: what the company's filings say about each topic, as a
history of revisions.

    GET /v1/companies/{ticker}/cards
    GET /v1/companies/{ticker}/cards/{kind}

Computed on read by `evident_memory.projection` from filings and entity
mentions; the card tables in `db/legacy-design` were never migrated, and a
derived read model does not need them. A card the stored data cannot fill says
why in `unavailable` rather than showing an empty history.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evident_db import Chunk, Company, Document, Entity, EntityMention
from evident_memory.cards import CardRevision, MemoryCard
from evident_memory.projection import (Cited, CitedEvidence, Filing,
                                       ProjectedCards, project_cards)
from evident_parser.anchors import paragraph_page

from ..deps import get_company, get_db
from ..schemas import (CardChangeOut, CardDeltaOut, CardDetailOut,
                       CardEvidenceOut, CardFactOut, CardRevisionOut,
                       MemoryCardOut)

router = APIRouter(prefix="/companies", tags=["cards"])


async def load_cards(db: AsyncSession, company: Company) -> ProjectedCards:
    filings = [Filing(document_id=d.id, accession=d.accession, form_type=d.form_type,
                      filed_at=d.filed_at, fiscal_period=d.fiscal_period)
               for d in (await db.execute(
                   select(Document).where(Document.company_id == company.id))).scalars()]
    rows = (await db.execute(
        select(Entity.slug, Entity.name, Entity.entity_type, EntityMention.document_id,
               EntityMention.chunk_id, Chunk.section_title, EntityMention.paragraph_id,
               EntityMention.page, EntityMention.quote)
        .join(Entity, Entity.id == EntityMention.entity_id)
        .outerjoin(Chunk, Chunk.id == EntityMention.chunk_id)
        .where(Entity.company_id == company.id))).all()
    cited = [Cited(slug=r.slug, name=r.name, entity_type=r.entity_type,
                   document_id=r.document_id, chunk_id=r.chunk_id,
                   section_title=r.section_title, paragraph_id=r.paragraph_id,
                   # the paragraph's own page, as the evidence resolver reports it
                   page=paragraph_page(r.paragraph_id, r.page), quote=r.quote)
             for r in rows]
    return project_cards(filings, cited)


def _evidence(e) -> CardEvidenceOut:
    extra = e if isinstance(e, CitedEvidence) else None
    return CardEvidenceOut(
        document_id=int(e.document_id), page_number=e.page_number,
        paragraph_id=e.paragraph_id, quote=e.quote,
        accession=extra.accession if extra else None,
        form_type=extra.form_type if extra else None,
        chunk_id=extra.chunk_id if extra else None,
        entity_slug=extra.entity_slug if extra else None,
        section_path=[extra.section_title] if extra and extra.section_title else [])


def _revision(r: CardRevision) -> CardRevisionOut:
    return CardRevisionOut(
        revision=r.revision, as_of=r.as_of, summary=r.summary, source_note=r.source_note,
        is_material=r.is_material,
        facts=[CardFactOut(key=f.key, label=f.label, value=f.value, unit=f.unit,
                           period=f.period, status=f.status) for f in r.facts],
        delta=CardDeltaOut(**{
            "added": [f.display() for f in r.delta.added],
            "removed": [f.display() for f in r.delta.removed],
            "changed": [CardChangeOut(label=b.label, before=b.value, after=a.value)
                        for b, a in r.delta.changed]}),
        evidence=[_evidence(e) for e in r.evidence])


def _card(card: MemoryCard, unavailable: str | None) -> MemoryCardOut:
    current = card.current
    return MemoryCardOut(
        kind=card.kind, title=card.title, source_label=card.source_label,
        revision_count=len(card.revisions), material_count=len(card.material_history),
        last_updated_at=current.as_of if current else None,
        current=_revision(current) if current else None, unavailable=unavailable)


@router.get("/{ticker}/cards", response_model=list[MemoryCardOut], summary="Memory cards")
async def list_cards(company: Company = Depends(get_company),
                     db: AsyncSession = Depends(get_db)) -> list[MemoryCardOut]:
    projected = await load_cards(db, company)
    # cards with a history first, in routing order; the rest after
    ordered = sorted(projected.cards.values(), key=lambda c: not c.revisions)
    return [_card(c, projected.unavailable.get(c.kind)) for c in ordered]


@router.get("/{ticker}/cards/{kind}", response_model=CardDetailOut,
            summary="One memory card, with its full history")
async def get_card(kind: str, company: Company = Depends(get_company),
                   db: AsyncSession = Depends(get_db),
                   materially: bool = Query(False, description="only revisions that "
                                                                "changed something")
                   ) -> CardDetailOut:
    projected = await load_cards(db, company)
    card = projected.cards.get(kind)
    if card is None:
        raise HTTPException(404, f"No card '{kind}'. Cards: {', '.join(projected.cards)}")
    history = card.material_history if materially else card.history
    return CardDetailOut(**_card(card, projected.unavailable.get(kind)).model_dump(),
                         history=[_revision(r) for r in history])


@router.get("/{ticker}/promises", summary="Promises — not built yet", status_code=501,
            responses={501: {"description": "Not built yet"}})
async def promises(ticker: str) -> None:
    # The promise lifecycle is designed and tested in evident_memory, but its
    # tables (db/legacy-design/002) were never migrated and nothing extracts
    # promises. 501 rather than 404: the route exists, the data does not.
    raise HTTPException(501, "Promises are not built yet: nothing extracts them and they "
                             "have no tables. See docs/api.md.")
