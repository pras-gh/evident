"""Typed data access.

Every write here is an **upsert keyed on natural identity**, not an insert. That
is the mechanism behind "update existing memory instead of duplicating topics":
the unique constraints in `models.py` make a duplicate a database error, and
these functions turn that error into the intended update.

Doing it with `ON CONFLICT` rather than select-then-insert also makes the
workers safe to run concurrently. A read-modify-write would race two ingests of
the same company into duplicate topics under load, which is exactly the failure
this design is supposed to prevent.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import (Chunk, Company, Document, Entity, EntityMention,
                     MetricObservation, Relationship, TimelineEvent)


# ------------------------------------------------------------------ company
def upsert_company(db: Session, *, cik: str, name: str,
                   ticker: str | None = None, sic: str | None = None) -> Company:
    stmt = (insert(Company)
            .values(cik=cik.zfill(10), name=name, ticker=ticker, sic=sic)
            .on_conflict_do_update(
                index_elements=[Company.cik],
                set_={"name": name, "ticker": func.coalesce(insert(Company).excluded.ticker,
                                                            Company.ticker),
                      "updated_at": func.now()})
            .returning(Company))
    return db.execute(stmt).scalar_one()


def company_by_ticker(db: Session, ticker: str) -> Company | None:
    return db.execute(
        select(Company).where(Company.ticker == ticker.upper())
    ).scalar_one_or_none()


# ----------------------------------------------------------------- document
def upsert_document(db: Session, *, company_id: int, accession: str,
                    form_type: str, filed_at: date, published_at: datetime,
                    source_url: str, source_format: str, content_sha256: str,
                    fiscal_period: str | None = None,
                    page_count: int | None = None) -> tuple[Document, bool]:
    """Returns (document, is_new_content).

    `is_new_content` is False when the digest matches what we already stored,
    which lets the ingest worker skip re-parsing bytes it has already seen.
    """
    existing = db.execute(
        select(Document).where(Document.accession == accession)
    ).scalar_one_or_none()
    if existing is not None and existing.content_sha256 == content_sha256:
        return existing, False

    stmt = (insert(Document)
            .values(company_id=company_id, accession=accession, form_type=form_type,
                    filed_at=filed_at, published_at=published_at,
                    source_url=source_url, source_format=source_format,
                    content_sha256=content_sha256, fiscal_period=fiscal_period,
                    page_count=page_count)
            .on_conflict_do_update(
                index_elements=[Document.accession],
                set_={"content_sha256": content_sha256, "page_count": page_count,
                      "form_type": form_type, "filed_at": filed_at})
            .returning(Document))
    return db.execute(stmt).scalar_one(), True


# -------------------------------------------------------------------- chunk
def replace_chunks(db: Session, *, document_id: int,
                   chunks: Sequence[dict]) -> int:
    """Replace a document's chunks wholesale.

    An amended filing can renumber or merge paragraphs, so patching in place
    would leave orphans that still answer queries. Deleting first is the only
    way to guarantee the chunk set matches the bytes we parsed.
    """
    db.query(Chunk).filter(Chunk.document_id == document_id).delete(
        synchronize_session=False)
    if not chunks:
        return 0
    db.execute(insert(Chunk), [{"document_id": document_id, **c} for c in chunks])
    return len(chunks)


def chunks_without_embeddings(db: Session, *, limit: int = 500,
                              company_id: int | None = None) -> list[Chunk]:
    stmt = select(Chunk).where(Chunk.embedding.is_(None)).order_by(Chunk.id).limit(limit)
    if company_id is not None:
        stmt = stmt.join(Document).where(Document.company_id == company_id)
    return list(db.execute(stmt).scalars())


def set_embeddings(db: Session, rows: Iterable[tuple[int, list[float]]], *,
                   provider: str, model: str) -> int:
    now = datetime.now(timezone.utc)
    payload = [{"id": cid, "embedding": vec, "embedding_provider": provider,
                "embedding_model": model, "embedded_at": now} for cid, vec in rows]
    if not payload:
        return 0
    db.bulk_update_mappings(Chunk, payload)
    return len(payload)


# -------------------------------------------------------------------- topic




# --------------------------------------------------------------------- risk




# ------------------------------------------------------------------- person


# ------------------------------------------------------------------- metric


def add_metric_observation(db: Session, *, entity_id: int, document_id: int,
                           period: str, value: float | None, unit: str | None,
                           chunk_id: int | None = None,
                           period_end: date | None = None,
                           page_number: int | None = None,
                           paragraph_id: str | None = None,
                           confidence: float | None = None) -> None:
    restated = db.execute(
        select(func.count()).select_from(MetricObservation)
        .where(MetricObservation.entity_id == entity_id,
               MetricObservation.period == period)
    ).scalar_one() > 0
    db.execute(insert(MetricObservation)
               .values(entity_id=entity_id, document_id=document_id, chunk_id=chunk_id,
                       period=period, period_end=period_end, value=value, unit=unit,
                       page_number=page_number, paragraph_id=paragraph_id,
                       confidence=confidence, is_restated=restated)
               .on_conflict_do_nothing(
                   index_elements=[MetricObservation.entity_id,
                                   MetricObservation.period,
                                   MetricObservation.document_id]))


# ------------------------------------------------------------------ entity
def upsert_entity(db: Session, *, company_id: int, entity_type: str, slug: str,
                  name: str, observed_at: date, description: str | None = None,
                  attributes: dict | None = None,
                  status: str | None = None) -> Entity:
    """Update, never duplicate — one path for every entity type.

    `first_seen` only moves earlier and `latest_seen` only later, so filings
    ingested out of order still produce the right span. Attributes are merged
    rather than replaced: a later filing that mentions a risk without restating
    its category must not erase the category.

    `entity_type` is deliberately **not** updated on conflict. Identity is
    `(company_id, slug)`, so a filing calling `Data Center` a product when it
    was stored as a segment would otherwise retype it, and the type would flap
    with ingest order — changing what every existing mention is understood to
    be evidence *of*. First write wins, and the caller can compare the returned
    row's `entity_type` against what it passed to see that it happened.
    """
    stmt = (insert(Entity)
            .values(company_id=company_id, entity_type=entity_type, slug=slug,
                    name=name, description=description,
                    attributes=attributes or {}, status=status or "active",
                    first_seen=observed_at, latest_seen=observed_at,
                    mention_count=0)
            .on_conflict_do_update(
                index_elements=[Entity.company_id, Entity.slug],
                set_={"name": name,
                      "description": func.coalesce(
                          insert(Entity).excluded.description, Entity.description),
                      "attributes": Entity.attributes.op("||")(
                          insert(Entity).excluded.attributes),
                      "first_seen": func.least(Entity.first_seen, observed_at),
                      "latest_seen": func.greatest(Entity.latest_seen, observed_at),
                      "updated_at": func.now()})
            .returning(Entity))
    return db.execute(stmt).scalar_one()


def add_entity_mention(db: Session, *, entity_id: int, document_id: int,
                       observed_at: date, quote: str,
                       chunk_id: int | None = None, page: int | None = None,
                       paragraph_id: str | None = None,
                       chunk_hash: str | None = None,
                       confidence: float | None = None) -> bool:
    """Returns True when the mention was new, so counts do not inflate on rerun."""
    stmt = (insert(EntityMention)
            .values(entity_id=entity_id, document_id=document_id, chunk_id=chunk_id,
                    observed_at=observed_at, quote=quote, page=page,
                    paragraph_id=paragraph_id, chunk_hash=chunk_hash,
                    confidence=confidence)
            .on_conflict_do_nothing(
                index_elements=[EntityMention.entity_id, EntityMention.chunk_id])
            .returning(EntityMention.id))
    inserted = db.execute(stmt).scalar_one_or_none() is not None
    if inserted:
        db.query(Entity).filter(Entity.id == entity_id).update(
            {Entity.mention_count: Entity.mention_count + 1},
            synchronize_session=False)
    return inserted


def mark_dropped_entities(db: Session, *, company_id: int, entity_type: str,
                          latest_filing_at: date) -> int:
    """An entity absent from the newest filing is dropped, not deleted.

    A risk factor quietly disappearing between two 10-Ks is one of the more
    informative things in the corpus, so the row and its mentions stay.
    """
    return db.query(Entity).filter(
        Entity.company_id == company_id, Entity.entity_type == entity_type,
        Entity.status == "active", Entity.latest_seen < latest_filing_at,
    ).update({Entity.status: "dropped"}, synchronize_session=False)


def upsert_relationship(db: Session, *, company_id: int, source_entity_id: int,
                        target_entity_id: int, relationship_type: str,
                        strength: float = 0.0,
                        document_ids: list[int] | None = None,
                        observed_at: date | None = None,
                        evidence_chunk_id: int | None = None,
                        attributes: dict | None = None) -> Relationship | None:
    """Edges are a materialisation of what the mentions already say, so a
    rebuild is safe and re-running only refreshes strength and span.

    `strength` is corpus-relative (0-1 against the strongest edge in this
    company's graph), which means it is only meaningful for a whole rebuild —
    writing one edge at a time with a strength computed against a stale maximum
    produces numbers that cannot be compared to each other.
    """
    if source_entity_id == target_entity_id:
        return None
    stmt = (insert(Relationship)
            .values(company_id=company_id, source_entity_id=source_entity_id,
                    target_entity_id=target_entity_id,
                    relationship_type=relationship_type, strength=strength,
                    evidence_chunk_id=evidence_chunk_id,
                    document_ids=document_ids or [], attributes=attributes or {},
                    first_seen=observed_at, latest_seen=observed_at)
            .on_conflict_do_update(
                index_elements=[Relationship.source_entity_id,
                                Relationship.target_entity_id,
                                Relationship.relationship_type],
                set_={"strength": strength, "document_ids": document_ids or [],
                      "evidence_chunk_id": func.coalesce(
                          insert(Relationship).excluded.evidence_chunk_id,
                          Relationship.evidence_chunk_id),
                      "first_seen": func.least(Relationship.first_seen,
                                               observed_at),
                      "latest_seen": func.greatest(Relationship.latest_seen,
                                                   observed_at),
                      "updated_at": func.now()})
            .returning(Relationship))
    return db.execute(stmt).scalar_one()


def entities_for_graph(db: Session, *, company_id: int,
                       entity_types: Sequence[str] | None = None) -> list[tuple]:
    """(entity, mention_count, distinct_documents) in one pass."""
    stmt = (select(Entity,
                   func.count(EntityMention.id),
                   func.count(func.distinct(EntityMention.document_id)))
            .outerjoin(EntityMention, EntityMention.entity_id == Entity.id)
            .where(Entity.company_id == company_id)
            .group_by(Entity.id))
    if entity_types:
        stmt = stmt.where(Entity.entity_type.in_(list(entity_types)))
    return list(db.execute(stmt).all())


# ----------------------------------------------------------------- timeline
def add_timeline_event(db: Session, *, company_id: int, kind: str, headline: str,
                       occurred_at: date, ref: str, detail: str | None = None,
                       document_id: int | None = None, chunk_id: int | None = None,
                       entity_id: int | None = None, page_number: int | None = None,
                       paragraph_id: str | None = None,
                       confidence: float | None = None) -> None:
    db.execute(insert(TimelineEvent)
               .values(company_id=company_id, kind=kind, headline=headline,
                       detail=detail, occurred_at=occurred_at, ref=ref,
                       document_id=document_id, chunk_id=chunk_id, entity_id=entity_id,
                       page_number=page_number, paragraph_id=paragraph_id,
                       confidence=confidence)
               .on_conflict_do_nothing(
                   index_elements=[TimelineEvent.company_id, TimelineEvent.kind,
                                   TimelineEvent.ref, TimelineEvent.occurred_at]))
