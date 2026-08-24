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

from .models import (Chunk, Company, Document, Metric, MetricObservation,
                     Person, Risk, TimelineEvent, Topic, TopicMention)


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
def upsert_topic(db: Session, *, company_id: int, slug: str, label: str,
                 observed_at: date) -> Topic:
    """The one the brief calls out: update, never duplicate.

    `first_seen_at` only ever moves earlier and `last_seen_at` only ever moves
    later, so ingesting filings out of order still produces the right span.
    """
    stmt = (insert(Topic)
            .values(company_id=company_id, slug=slug, label=label,
                    first_seen_at=observed_at, last_seen_at=observed_at,
                    mention_count=0)
            .on_conflict_do_update(
                index_elements=[Topic.company_id, Topic.slug],
                set_={"label": label,
                      "first_seen_at": func.least(Topic.first_seen_at, observed_at),
                      "last_seen_at": func.greatest(Topic.last_seen_at, observed_at),
                      "updated_at": func.now()})
            .returning(Topic))
    return db.execute(stmt).scalar_one()


def add_topic_mention(db: Session, *, topic_id: int, chunk_id: int,
                      document_id: int, observed_at: date, quote: str) -> bool:
    """Returns True when the mention was new.

    Re-running the builder over the same chunk must not inflate mention_count,
    so the counter is only bumped on a genuine insert.
    """
    # RETURNING, not rowcount: after ON CONFLICT DO NOTHING the driver's
    # rowcount is not a dependable "did this insert" signal, and reading it as
    # one leaves mention_count permanently at zero — a bug that looks like
    # "extraction found nothing" rather than like a bug.
    stmt = (insert(TopicMention)
            .values(topic_id=topic_id, chunk_id=chunk_id, document_id=document_id,
                    observed_at=observed_at, quote=quote)
            .on_conflict_do_nothing(
                index_elements=[TopicMention.topic_id, TopicMention.chunk_id])
            .returning(TopicMention.id))
    inserted = db.execute(stmt).scalar_one_or_none() is not None
    if inserted:
        db.query(Topic).filter(Topic.id == topic_id).update(
            {Topic.mention_count: Topic.mention_count + 1},
            synchronize_session=False)
    return inserted


# --------------------------------------------------------------------- risk
def upsert_risk(db: Session, *, company_id: int, slug: str, label: str,
                observed_at: date, chunk_id: int | None = None,
                category: str | None = None, severity: str | None = None) -> Risk:
    stmt = (insert(Risk)
            .values(company_id=company_id, slug=slug, label=label, category=category,
                    severity=severity, chunk_id=chunk_id, status="active",
                    first_seen_at=observed_at, last_seen_at=observed_at)
            .on_conflict_do_update(
                index_elements=[Risk.company_id, Risk.slug],
                set_={"label": label, "status": "active",
                      "last_seen_at": func.greatest(Risk.last_seen_at, observed_at),
                      "updated_at": func.now()})
            .returning(Risk))
    return db.execute(stmt).scalar_one()


def mark_dropped_risks(db: Session, *, company_id: int,
                       latest_filing_at: date) -> int:
    """A risk absent from the newest filing is dropped, not deleted.

    The disappearance of a risk factor between two 10-Ks is one of the more
    informative things in the corpus, so the row and its history stay.
    """
    return db.query(Risk).filter(
        Risk.company_id == company_id,
        Risk.status == "active",
        Risk.last_seen_at < latest_filing_at,
    ).update({Risk.status: "dropped"}, synchronize_session=False)


# ------------------------------------------------------------------- person
def upsert_person(db: Session, *, company_id: int, full_name: str,
                  normalised: str, observed_at: date,
                  roles: dict | None = None, chunk_id: int | None = None) -> Person:
    stmt = (insert(Person)
            .values(company_id=company_id, full_name=full_name, normalised=normalised,
                    roles=roles, chunk_id=chunk_id,
                    first_seen_at=observed_at, last_seen_at=observed_at)
            .on_conflict_do_update(
                index_elements=[Person.company_id, Person.normalised],
                # keep the longest spelling — usually the one with a middle name
                set_={"full_name": func.greatest(Person.full_name, full_name),
                      "last_seen_at": func.greatest(Person.last_seen_at, observed_at),
                      "updated_at": func.now()})
            .returning(Person))
    return db.execute(stmt).scalar_one()


# ------------------------------------------------------------------- metric
def upsert_metric(db: Session, *, company_id: int, name: str, normalised: str,
                  unit: str | None = None) -> Metric:
    stmt = (insert(Metric)
            .values(company_id=company_id, name=name, normalised=normalised, unit=unit)
            .on_conflict_do_update(
                index_elements=[Metric.company_id, Metric.normalised],
                set_={"name": name})
            .returning(Metric))
    return db.execute(stmt).scalar_one()


def add_metric_observation(db: Session, *, metric_id: int, document_id: int,
                           period: str, value: float | None, unit: str | None,
                           chunk_id: int | None = None,
                           period_end: date | None = None) -> None:
    restated = db.execute(
        select(func.count()).select_from(MetricObservation)
        .where(MetricObservation.metric_id == metric_id,
               MetricObservation.period == period)
    ).scalar_one() > 0
    db.execute(insert(MetricObservation)
               .values(metric_id=metric_id, document_id=document_id, chunk_id=chunk_id,
                       period=period, period_end=period_end, value=value, unit=unit,
                       is_restated=restated)
               .on_conflict_do_nothing(
                   index_elements=[MetricObservation.metric_id,
                                   MetricObservation.period,
                                   MetricObservation.document_id]))


# ----------------------------------------------------------------- timeline
def add_timeline_event(db: Session, *, company_id: int, kind: str, headline: str,
                       occurred_at: date, ref: str, detail: str | None = None,
                       document_id: int | None = None, chunk_id: int | None = None,
                       topic_id: int | None = None) -> None:
    db.execute(insert(TimelineEvent)
               .values(company_id=company_id, kind=kind, headline=headline,
                       detail=detail, occurred_at=occurred_at, ref=ref,
                       document_id=document_id, chunk_id=chunk_id, topic_id=topic_id)
               .on_conflict_do_nothing(
                   index_elements=[TimelineEvent.company_id, TimelineEvent.kind,
                                   TimelineEvent.ref, TimelineEvent.occurred_at]))
