"""Memory builder — turns chunks into resolved company memory.

Extraction is per document and knows nothing about history. This worker folds
those results into one entity per real-world thing.

"Updates existing memory instead of duplicating topics" is not implemented as a
check here. It is implemented as a unique constraint on `(company_id, slug)` plus
`ON CONFLICT DO UPDATE` in the repository layer, so a duplicate is impossible
rather than merely unlikely — including under two workers running at once.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from evident_ai.extract import extract_from_blocks
from evident_db import Chunk, Document, session_scope
from evident_db.repositories import (add_metric_observation, add_timeline_event,
                                     add_topic_mention, mark_dropped_risks,
                                     upsert_metric, upsert_person, upsert_risk,
                                     upsert_topic)
from evident_memory.entities import (normalise_metric, normalise_person,
                                     slugify)
from evident_parser.models import Block

log = logging.getLogger("evident.memory_builder")


@dataclass(slots=True)
class BuildStats:
    documents: int = 0
    topics: int = 0
    mentions_new: int = 0
    mentions_seen: int = 0
    people: int = 0
    risks: int = 0
    metrics: int = 0
    events: int = 0
    dropped_uncited: int = 0
    risks_marked_dropped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in self.__dataclass_fields__.values()}


def build_for_document(db: Session, *, company_id: int, document: Document,
                       client: Any | None = None,
                       stats: BuildStats | None = None) -> BuildStats:
    stats = stats or BuildStats()
    chunks = list(db.execute(
        select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal)
    ).scalars())
    if not chunks:
        return stats

    by_paragraph = {(c.paragraph_ids or [c.chunk_hash])[0]: c for c in chunks}
    blocks = []
    for c in chunks:
        block = Block(paragraph_id=(c.paragraph_ids or [c.chunk_hash])[0],
                      ordinal=c.ordinal, text=c.text, page_number=c.page_number)
        # carried so extracted entities can record the chunk they came from
        block.chunk_hash = c.chunk_hash            # type: ignore[attr-defined]
        blocks.append(block)

    entities, report = extract_from_blocks(
        blocks, document_id=str(document.id),
        observed_at=document.filed_at, client=client)
    stats.documents += 1
    stats.dropped_uncited += report.dropped
    if report.dropped:
        # The one failure that looks like success — loud on purpose.
        log.warning("dropped %d uncited entities from %s (ids: %s)",
                    report.dropped, document.accession, report.bad_ids[:5])

    for topic in entities.get("topics", []):
        row = upsert_topic(db, company_id=company_id, slug=topic.slug,
                           label=topic.label, observed_at=document.filed_at)
        stats.topics += 1
        for ev in topic.evidence:
            chunk = by_paragraph.get(ev.paragraph_id or "")
            if chunk is None:
                continue
            is_new = add_topic_mention(db, topic_id=row.id, chunk_id=chunk.id,
                                       document_id=document.id,
                                       observed_at=document.filed_at, quote=ev.quote,
                                       page_number=ev.page_number,
                                       paragraph_id=ev.paragraph_id,
                                       confidence=ev.confidence)
            stats.mentions_new += is_new
            stats.mentions_seen += not is_new
            if is_new and row.first_seen_at == document.filed_at:
                add_timeline_event(db, company_id=company_id, kind="topic",
                                   headline=f"{topic.label} first appears",
                                   occurred_at=document.filed_at,
                                   ref=f"topic:{topic.slug}", document_id=document.id,
                                   chunk_id=chunk.id, topic_id=row.id,
                                   page_number=ev.page_number,
                                   paragraph_id=ev.paragraph_id,
                                   confidence=ev.confidence)
                stats.events += 1

    for person in entities.get("people", []):
        chunk = _chunk_for(person, by_paragraph)
        prov = _provenance(person)
        upsert_person(db, company_id=company_id, full_name=person.full_name,
                      normalised=normalise_person(person.full_name),
                      observed_at=document.filed_at,
                      chunk_id=chunk.id if chunk else None, **prov)
        stats.people += 1

    for risk in entities.get("risks", []):
        chunk = _chunk_for(risk, by_paragraph)
        upsert_risk(db, company_id=company_id, slug=risk.slug, label=risk.label,
                    category=risk.category, observed_at=document.filed_at,
                    chunk_id=chunk.id if chunk else None, **_provenance(risk))
        stats.risks += 1

    pages = {c.paragraph_ids[0] if c.paragraph_ids else c.chunk_hash: c.page_number
             for c in chunks}
    for m in entities.get("metrics_raw", []):
        metric = upsert_metric(db, company_id=company_id, name=m.name,
                               normalised=normalise_metric(m.name), unit=m.unit)
        chunk = by_paragraph.get(m.paragraph_id)
        add_metric_observation(db, metric_id=metric.id, document_id=document.id,
                               chunk_id=chunk.id if chunk else None,
                               period=m.period or "unknown", value=m.value,
                               unit=m.unit, page_number=pages.get(m.paragraph_id),
                               paragraph_id=m.paragraph_id,
                               confidence=getattr(m, "confidence", None))
        stats.metrics += 1

    add_timeline_event(db, company_id=company_id, kind="filing",
                       headline=f"{document.form_type} filed",
                       occurred_at=document.filed_at,
                       ref=f"document:{document.id}", document_id=document.id)
    stats.events += 1
    return stats


def _provenance(entity) -> dict:
    """The provenance every extracted object carries."""
    for ev in getattr(entity, "evidence", []) or []:
        return {"page_number": ev.page_number, "paragraph_id": ev.paragraph_id,
                "confidence": ev.confidence}
    return {"page_number": None, "paragraph_id": None, "confidence": None}


def _chunk_for(entity, by_paragraph):
    for ev in getattr(entity, "evidence", []) or []:
        chunk = by_paragraph.get(ev.paragraph_id or "")
        if chunk is not None:
            return chunk
    return None


def run(*, company_id: int, url: str | None = None,
        client: Any | None = None) -> BuildStats:
    stats = BuildStats()
    with session_scope(url) as db:
        documents = list(db.execute(
            select(Document).where(Document.company_id == company_id)
            .order_by(Document.filed_at)
        ).scalars())
        for document in documents:
            build_for_document(db, company_id=company_id, document=document,
                               client=client, stats=stats)
        if documents:
            stats.risks_marked_dropped = mark_dropped_risks(
                db, company_id=company_id,
                latest_filing_at=max(d.filed_at for d in documents))
    return stats
