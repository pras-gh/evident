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
from evident_db import Chunk, Document, Entity, session_scope
from evident_db.repositories import (add_entity_mention, add_metric_observation,
                                     add_timeline_event, mark_dropped_entities,
                                     upsert_entity, upsert_relationship)
from evident_graph.normalize import display_label, entity_key
from evident_memory.entities import normalise_metric, normalise_person
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
    products: int = 0
    events: int = 0
    relationships: int = 0
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

    def record(kind: str, key: str, label: str, evidence, *,
               attributes: dict | None = None) -> int | None:
        """One path for every kind — the point of the unified model."""
        row = upsert_entity(db, company_id=company_id, kind=kind, key=key,
                            label=display_label(key, label),
                            observed_at=document.filed_at,
                            attributes=attributes or {})
        db.flush()
        first = None
        for ev in evidence or []:
            chunk = by_paragraph.get(ev.paragraph_id or "")
            is_new = add_entity_mention(
                db, entity_id=row.id, document_id=document.id,
                chunk_id=chunk.id if chunk else None,
                observed_at=document.filed_at, quote=ev.quote,
                page_number=ev.page_number, paragraph_id=ev.paragraph_id,
                chunk_hash=chunk.chunk_hash if chunk else None,
                confidence=ev.confidence)
            stats.mentions_new += is_new
            stats.mentions_seen += not is_new
            first = first or ev
        return row.id

    for topic in entities.get("topics", []):
        record("topic", entity_key(topic.label), topic.label, topic.evidence)
        stats.topics += 1

    for person in entities.get("people", []):
        record("person", normalise_person(person.full_name), person.full_name,
               person.evidence)
        stats.people += 1

    for product in entities.get("products", []):
        record("product", entity_key(product.name), product.name,
               product.evidence, attributes={"status": product.status})
        stats.products += 1

    for risk in entities.get("risks", []):
        record("risk", entity_key(risk.label), risk.label, risk.evidence,
               attributes={"category": risk.category} if risk.category else {})
        stats.risks += 1

    pages = {(c.paragraph_ids or [c.chunk_hash])[0]: c.page_number for c in chunks}
    for m in entities.get("metrics_raw", []):
        entity_id = upsert_entity(db, company_id=company_id, kind="metric",
                                  key=normalise_metric(m.name), label=m.name,
                                  observed_at=document.filed_at,
                                  attributes={"unit": m.unit} if m.unit else {}).id
        db.flush()
        chunk = by_paragraph.get(m.paragraph_id)
        add_metric_observation(db, entity_id=entity_id, document_id=document.id,
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
            stats.risks_marked_dropped = mark_dropped_entities(
                db, company_id=company_id, kind="risk",
                latest_filing_at=max(d.filed_at for d in documents))
    return stats
