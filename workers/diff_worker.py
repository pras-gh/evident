"""Diff worker — projects resolved memory onto memory cards.

Runs after the memory worker. For each filing, works out which cards it touched
(routing is a predicate over document, section and speaker) and appends one
revision per card with a diff against the previous one.

Appending is the whole job. Nothing here updates a card in place, because an
in-place update destroys the history that makes a card different from a stat
tile — and a filing that touched a card without moving anything still earns a
revision, marked immaterial.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from evident_memory.cards import (CAPEX, GUIDANCE, PRODUCTS, REVENUE, RISKS,
                                  CardFact, MemoryCard, build_cards,
                                  facts_for_guidance, facts_for_metric,
                                  facts_for_products, facts_for_risks, route)
from evident_memory.entities import CompanyMemory

log = logging.getLogger("evident.diff_worker")


@dataclass(slots=True)
class FilingContext:
    """What routing needs to know about one filing."""
    document_id: str
    as_of: date
    form_type: str | None = None
    doc_kind: str | None = None
    section_titles: Sequence[str] = ()
    speaker_roles: Sequence[str] = ()
    source_note: str | None = None


@dataclass(slots=True)
class DiffResult:
    cards: dict[str, MemoryCard]
    revisions_written: int
    material: int


def apply_filings(memory: CompanyMemory,
                  filings: Sequence[FilingContext]) -> DiffResult:
    cards = build_cards()
    written = material = 0

    for filing in sorted(filings, key=lambda f: f.as_of):
        touched: list[str] = []
        for section in (filing.section_titles or [None]):
            for speaker in (filing.speaker_roles or [None]):
                touched.extend(route(form_type=filing.form_type,
                                     doc_kind=filing.doc_kind,
                                     section_title=section,
                                     speaker_role=speaker))
        for kind in dict.fromkeys(touched):          # de-dupe, keep order
            facts = _facts_for(kind, memory)
            revision = cards[kind].apply(
                as_of=filing.as_of, document_id=filing.document_id,
                facts=facts, source_note=filing.source_note,
            )
            if revision is None:
                continue                              # already applied
            written += 1
            material += bool(revision.is_material)
            log.info("card %s rev %d (%s): %s", kind, revision.revision,
                     "material" if revision.is_material else "no change",
                     revision.summary)
    return DiffResult(cards, written, material)


def _facts_for(kind: str, memory: CompanyMemory) -> list[CardFact]:
    if kind == REVENUE:
        return facts_for_metric(memory.metrics, "revenue")
    if kind == CAPEX:
        return facts_for_metric(memory.metrics, "capital expenditures")
    if kind == PRODUCTS:
        return facts_for_products(memory.products)
    if kind == RISKS:
        return facts_for_risks(memory.risks)
    if kind == GUIDANCE:
        return facts_for_guidance(memory.promises)
    return []
