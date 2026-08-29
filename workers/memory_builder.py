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

from evident_ai.extract import ExtractionRejected, extract_from_blocks
from evident_db import Chunk, Document, Entity, session_scope
from evident_db.repositories import (add_entity_mention, add_metric_observation,
                                     add_timeline_event, mark_dropped_entities,
                                     upsert_entity, upsert_relationship)
from evident_graph.normalize import display_label
from evident_graph.taxonomy import slug as entity_slug
from evident_parser.models import Block

log = logging.getLogger("evident.memory_builder")


@dataclass(slots=True)
class BuildStats:
    documents: int = 0
    entities: int = 0
    mentions_new: int = 0
    mentions_seen: int = 0
    metrics: int = 0
    events: int = 0
    relationships: int = 0
    dropped_uncited: int = 0
    risks_marked_dropped: int = 0
    #: whole responses refused — distinct from individual items dropped
    responses_rejected: int = 0
    #: A name already stored under a different type. First write wins, so this
    #: counts the extractions that were overruled — a high number means the
    #: taxonomy is ambiguous for this corpus, not that the run failed.
    type_conflicts: int = 0
    by_type: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in self.__dataclass_fields__.values()}


def build_for_document(db: Session, *, company_id: int, document: Document,
                       client: Any | None = None,
                       stats: BuildStats | None = None,
                       limit: int | None = None) -> BuildStats:
    """Extract one document into memory.

    `limit` caps how many chunks are sent, for a cheap first run against the
    real API. It is a real parameter rather than a caller-side slice because
    this function owns the chunk query; slicing outside it would silently do
    nothing.
    """
    stats = stats or BuildStats()
    stmt = (select(Chunk).where(Chunk.document_id == document.id)
            .order_by(Chunk.ordinal))
    if limit is not None:
        stmt = stmt.limit(limit)
    chunks = list(db.execute(stmt).scalars())
    if not chunks:
        return stats

    by_paragraph = {(c.paragraph_ids or [c.chunk_hash])[0]: c for c in chunks}
    # Block is a slots dataclass, so nothing can be stapled onto it. The chunk
    # an entity came from is recovered through `by_paragraph` instead, which is
    # the same lookup the mention write already uses.
    blocks = [Block(paragraph_id=(c.paragraph_ids or [c.chunk_hash])[0],
                    ordinal=c.ordinal, text=c.text, page_number=c.page_number)
              for c in chunks]

    stats.documents += 1
    try:
        result = extract_from_blocks(blocks, client=client)
    except ExtractionRejected as exc:
        # The whole response was unusable, so none of it is stored. Loud, and
        # the document is left untouched rather than half-populated.
        stats.responses_rejected += 1
        log.error("rejected extraction for %s: %s", document.accession, exc.reason)
        return stats

    extracted, report = result.entities, result.report
    stats.dropped_uncited += report.dropped
    if report.dropped:
        # The one failure that looks like success — loud on purpose.
        log.warning("dropped %d items from %s (ids: %s; first reason: %s)",
                    report.dropped, document.accession, report.bad_ids[:5],
                    report.reasons[0] if report.reasons else "-")

    pages = {(c.paragraph_ids or [c.chunk_hash])[0]: c.page_number for c in chunks}

    # One upsert per distinct entity, one mention per citation. Grouping first
    # means an entity named in six paragraphs is written once and cited six
    # times, rather than fighting the unique constraint six times.
    by_slug: dict[str, list] = {}
    for item in extracted:
        by_slug.setdefault(entity_slug(item.name), []).append(item)

    entity_ids: dict[str, int] = {}
    for slug, items in by_slug.items():
        head = items[0]
        row = upsert_entity(
            db, company_id=company_id, entity_type=head.entity_type, slug=slug,
            name=display_label(slug, head.name),
            description=next((i.description for i in items if i.description), None),
            observed_at=document.filed_at)
        db.flush()
        entity_ids[slug] = row.id
        stats.entities += 1
        stats.by_type[row.entity_type] = stats.by_type.get(row.entity_type, 0) + 1

        if row.entity_type != head.entity_type:
            # Identity is (company_id, slug), so the stored type wins and the
            # extraction is overruled. Loud because it means one name is being
            # read two ways, which is a taxonomy problem, not a data problem.
            stats.type_conflicts += 1
            log.warning("%s: extracted as %s but stored as %s — keeping stored",
                        slug, head.entity_type, row.entity_type)

        for item in items:
            chunk = by_paragraph.get(item.paragraph_id)
            is_new = add_entity_mention(
                db, entity_id=row.id, document_id=document.id,
                chunk_id=chunk.id if chunk else None,
                observed_at=document.filed_at, quote=item.quote,
                page=pages.get(item.paragraph_id), paragraph_id=item.paragraph_id,
                chunk_hash=chunk.chunk_hash if chunk else None,
                confidence=item.confidence)
            stats.mentions_new += is_new
            stats.mentions_seen += not is_new

            # A metric that names a figure is also an observation: a period and
            # a number, which belong in a time series rather than in a quote.
            if item.entity_type == "metric" and item.value is not None:
                add_metric_observation(
                    db, entity_id=row.id, document_id=document.id,
                    chunk_id=chunk.id if chunk else None,
                    period=item.period or "unknown", value=item.value,
                    unit=item.unit, page_number=pages.get(item.paragraph_id),
                    paragraph_id=item.paragraph_id, confidence=item.confidence)
                stats.metrics += 1

    # ------------------------------------------------------- relationships
    # Asserted edges, resolved from names to ids now that every entity in this
    # response has a row. Endpoints were already checked to exist during
    # validation; a miss here means the entity upsert collapsed two names onto
    # one slug, which is legitimate and just leaves fewer edges.
    ids_by_slug = {slug: row_id for slug, row_id in entity_ids.items()}
    for r in result.relationships:
        src = ids_by_slug.get(entity_slug(r.source_name))
        dst = ids_by_slug.get(entity_slug(r.target_name))
        if src is None or dst is None or src == dst:
            continue
        chunk = by_paragraph.get(r.paragraph_id)
        edge = upsert_relationship(
            db, company_id=company_id, source_entity_id=src,
            target_entity_id=dst, relationship_type=r.relationship_type,
            # The model's confidence, not a corpus-relative score. Both are
            # 0-1, and a graph rebuild recomputes strength for the edges it
            # derives — `asserted` marks the ones it must not overwrite.
            strength=r.confidence, observed_at=document.filed_at,
            evidence_chunk_id=chunk.id if chunk else None,
            document_ids=[document.id],
            attributes={"asserted": True, "quote": r.quote,
                        "paragraph_id": r.paragraph_id})
        if edge is not None:
            stats.relationships += 1

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
