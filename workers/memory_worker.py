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

from evident_ai.extract import ExtractionRejected, extract_from_blocks
from evident_memory.entities import (CompanyMemory, DocumentRef, Evidence,
                                     Person, Product, Risk, Topic)
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


def _legacy(extracted: Sequence[Any], ref: Any,
            pages: dict[str, int | None]) -> dict[str, list]:
    """Project canonical entities onto the pre-Phase-1 memory types.

    `CompanyMemory` and the `merge_*` resolvers still speak the older
    vocabulary — Topic, Person, Product, Risk — and folding them onto the
    canonical model is a larger change than Phase 1. This keeps the projection
    honest in the meantime by mapping rather than reimplementing.

    Two mappings are lossy on purpose. `strategy` becomes `Topic` because topic
    was always the catch-all it replaced. And **promises are no longer
    extracted at all**: there is no `promise` type in the canonical eight, so
    `resolve_promises` now runs on an empty list and every promise settles to
    `unclear`. The promise layer needs its own type before it works again.
    """
    out: dict[str, list] = {"topics": [], "people": [], "products": [],
                            "risks": [], "metrics": []}
    for item in extracted:
        ev = Evidence(document_id=ref.document_id, paragraph_id=item.paragraph_id,
                      page_number=pages.get(item.paragraph_id), quote=item.quote,
                      observed_at=ref.filed_date, confidence=item.confidence)
        if item.entity_type == "strategy":
            out["topics"].append(Topic(item.slug, item.name, ref.filed_date,
                                       ref.filed_date, [ev]))
        elif item.entity_type == "executive":
            out["people"].append(Person(item.name, item.slug, [],
                                        ref.filed_date, ref.filed_date, [ev]))
        elif item.entity_type == "product":
            out["products"].append(Product(item.name, item.slug, "mentioned",
                                           ref.filed_date, ref.filed_date, [ev]))
        elif item.entity_type == "risk":
            out["risks"].append(Risk(item.slug, item.name, None, "active",
                                     ref.filed_date, ref.filed_date, [ev]))
        elif item.entity_type == "metric" and item.value is not None:
            out["metrics"].append((item, ev))
    return out


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
    dropped = rejected = 0

    for ref, blocks in sorted(documents, key=lambda d: d[0].filed_date):
        memory.documents.append(ref)
        try:
            result = extract_from_blocks(blocks, client=client)
        except ExtractionRejected as exc:
            # Nothing from an unusable response is kept; the document simply
            # contributes no entities to the projection.
            rejected += 1
            log.error("rejected extraction for %s: %s", ref.accession, exc.reason)
            continue
        extracted, report = result.entities, result.report
        dropped += report.dropped
        if report.dropped:
            # A rising drop rate means extraction started inventing citations.
            # Loud, because it is the one failure that looks like success.
            log.warning("dropped %d uncited entities from %s (bad ids: %s)",
                        report.dropped, ref.accession, report.bad_ids[:5])
        pages = {b.paragraph_id: b.page_number for b in blocks}
        legacy = _legacy(extracted, ref, pages)
        topics.append(legacy["topics"])
        people.append(legacy["people"])
        products.append(legacy["products"])
        risks.append(legacy["risks"])
        metric_rows.extend(_metric_rows(legacy["metrics"], ref))

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


def _metric_rows(rows: Sequence[tuple], ref: DocumentRef):
    """The row shape `merge_metrics` expects, from (Extracted, Evidence) pairs."""
    for item, ev in rows:
        yield (item.name, item.period, item.value, item.unit, ev, None)
