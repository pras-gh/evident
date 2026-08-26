"""Memory worker — turns parsed filings into resolved company memory.

Runs after the ingest worker. Extraction is per document and knows nothing about
history; this worker folds those results into one entity per real-world thing
and carries promises forward until something settles them.

Idempotent: re-running over the same documents produces the same memory, because
entity identity comes from normalised names and content-addressed evidence
rather than insertion order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

from evident_ai.extract import extract_from_blocks
from evident_memory.entities import CompanyMemory, DocumentRef
from evident_memory.resolve import (build_timeline, merge_metrics, merge_people,
                                    merge_products, merge_risks, merge_topics,
                                    resolve_promises)
from evident_parser.models import Block

log = logging.getLogger("evident.memory_worker")


@dataclass(slots=True)
class BuildResult:
    memory: CompanyMemory
    documents_seen: int
    entities_dropped: int


def build_memory(
    *,
    company_id: str,
    ticker: str | None,
    documents: Sequence[tuple[DocumentRef, Sequence[Block]]],
    client: Any | None = None,
    as_of: date | None = None,
) -> BuildResult:
    """Extract from each document, then resolve across all of them."""
    as_of = as_of or date.today()
    memory = CompanyMemory(company_id=company_id, ticker=ticker)
    topics, people, products, risks, promises, metric_rows = [], [], [], [], [], []
    dropped = 0

    for ref, blocks in sorted(documents, key=lambda d: d[0].filed_date):
        memory.documents.append(ref)
        entities, report = extract_from_blocks(
            blocks, document_id=ref.document_id,
            observed_at=ref.filed_date, client=client,
        )
        dropped += report.dropped
        if report.dropped:
            # A rising drop rate means extraction started inventing citations.
            # Loud, because it is the one failure that looks like success.
            log.warning("dropped %d uncited entities from %s (bad ids: %s)",
                        report.dropped, ref.accession, report.bad_ids[:5])
        topics.append(entities.get("topics", []))
        people.append(entities.get("people", []))
        products.append(entities.get("products", []))
        risks.append(entities.get("risks", []))
        promises.extend(entities.get("promises", []))
        metric_rows.extend(_metric_rows(entities, ref))

    latest = max((d.filed_date for d in memory.documents), default=None)
    memory.topics = merge_topics(topics)
    memory.people = merge_people(people)
    memory.products = merge_products(products)
    memory.risks = merge_risks(risks, latest_filing_date=latest)
    memory.metrics = merge_metrics(metric_rows)
    # Signals come from a later pass over filings published after each promise;
    # with none supplied, overdue promises settle to `unclear` rather than being
    # asserted broken.
    memory.promises = resolve_promises(promises, [], as_of=as_of)
    memory.timeline = build_timeline(memory)
    return BuildResult(memory, len(memory.documents), dropped)


def _metric_rows(entities: dict, ref: DocumentRef):
    meta = entities.get("_evidence_for", {})
    pages = meta.get("pages", {})
    for m in entities.get("metrics_raw", []):
        from evident_memory.entities import Evidence
        yield (m.name, m.period, m.value, m.unit,
               Evidence(document_id=ref.document_id, paragraph_id=m.paragraph_id,
                        page_number=pages.get(m.paragraph_id), quote=m.quote,
                        observed_at=ref.filed_date),
               None)
