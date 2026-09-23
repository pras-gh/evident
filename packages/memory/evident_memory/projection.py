"""Memory cards, projected from what the database stores today.

The card model (`cards.py`) was designed ahead of its storage: its tables are
in `db/legacy-design/003_memory_cards.sql` and were never migrated. Cards are
a derived read model anyway — rebuilding one is deterministic — so they are
computed on read here, from filings and the entity mentions extraction
wrote, the same way the timeline is. Nothing to backfill, nothing to drift.

What the stored data can honestly fill:

    Risks        risk entities cited in a filing's Item 1A
    Litigation   entities cited in a filing's Legal Proceedings (Item 3)

and what it cannot, which the card says instead of showing an empty history
that reads as "nothing happened":

    Revenue, CapEx   need metric values; nothing extracts them yet
    Products         routes on earnings-call transcripts; none are ingested
    Guidance         routes on CEO statements in transcripts; none are ingested

Two rules shared with the timeline, for the same reasons:

* **Annual filings only**, so a 10-Q's partial risk factors are never diffed
  against a 10-K's full set — every quarter would "drop" most of them.
* **No revision without facts.** A filing whose extraction found nothing in the
  section adds no revision; an empty revision would diff as every risk
  factor dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .cards import (CAPEX, DEFAULT_SOURCES, GUIDANCE, LITIGATION, PRODUCTS,
                    RISKS, REVENUE, CardFact, CardSource, MemoryCard,
                    build_cards)
from .entities import Evidence

ANNUAL_FORMS = frozenset({"10-K", "20-F", "40-F"})

#: Why a card has no history, for the cards the stored data cannot fill.
UNAVAILABLE = {
    REVENUE: "Needs reported metric values, and nothing extracts them yet.",
    CAPEX: "Needs reported metric values, and nothing extracts them yet.",
    PRODUCTS: "Updates from earnings-call transcripts; only SEC filings are ingested.",
    GUIDANCE: "Updates from CEO statements in transcripts; only SEC filings are ingested.",
}

_SECTION_NAME = {RISKS: "Risk Factors section", LITIGATION: "Legal Proceedings section"}


@dataclass(frozen=True, slots=True)
class Filing:
    document_id: int
    accession: str
    form_type: str
    filed_at: date
    fiscal_period: str | None = None


@dataclass(frozen=True, slots=True)
class Cited:
    """One entity mention, with where it sits."""
    slug: str
    name: str
    entity_type: str
    document_id: int
    chunk_id: int | None
    section_title: str | None
    paragraph_id: str | None
    page: int | None
    quote: str


@dataclass(frozen=True, slots=True)
class CitedEvidence(Evidence):
    """Evidence plus what a reader needs to open it: the filing and chunk."""
    accession: str | None = None
    form_type: str | None = None
    chunk_id: int | None = None
    section_title: str | None = None
    #: the entity the fact is about, so the evidence viewer can open it
    entity_slug: str | None = None


@dataclass(slots=True)
class ProjectedCards:
    cards: dict[str, MemoryCard]
    #: kind → why the card has no history; absent for cards that have one
    unavailable: dict[str, str]


_POSITION = re.compile(r"^(\d+)_(\d+)(?:#(\d+))?$")


def _reading_order(c: Cited) -> tuple:
    hit = _POSITION.match(c.paragraph_id or "")
    if hit:
        return (0, int(hit[1]), int(hit[2]), int(hit[3] or 0), c.chunk_id or 0)
    return (1, c.page or 0, 0, 0, c.chunk_id or 0)


def _facts(kind: str, filing: Filing, mentions: list[Cited]) -> list[CardFact]:
    """One fact per entity, most-discussed first; evidence is its first
    paragraph in reading order."""
    by_entity: dict[str, list[Cited]] = {}
    for m in mentions:
        by_entity.setdefault(m.slug, []).append(m)

    def paragraphs(ms: list[Cited]) -> int:
        return len({(m.paragraph_id or f"c{m.chunk_id}").split("#")[0] for m in ms})

    prefix = "risk" if kind == RISKS else "matter"
    facts = []
    for slug, ms in sorted(by_entity.items(),
                           key=lambda kv: (-paragraphs(kv[1]), kv[1][0].name.lower())):
        first = min(ms, key=_reading_order)
        facts.append(CardFact(
            key=f"{prefix}:{slug}", label=first.name,
            evidence=CitedEvidence(
                document_id=str(filing.document_id), paragraph_id=first.paragraph_id,
                page_number=first.page, quote=first.quote, observed_at=filing.filed_at,
                accession=filing.accession, form_type=filing.form_type,
                chunk_id=first.chunk_id, section_title=first.section_title,
                entity_slug=slug)))
    return facts


def project_cards(filings: Sequence[Filing], cited: Iterable[Cited], *,
                  sources: Sequence[CardSource] = DEFAULT_SOURCES) -> ProjectedCards:
    cards = build_cards(sources=sources)
    by_kind = {s.card_kind: s for s in sources}

    per_doc: dict[int, list[Cited]] = {}
    for c in cited:
        per_doc.setdefault(c.document_id, []).append(c)

    annual = sorted((f for f in filings if f.form_type in ANNUAL_FORMS),
                    key=lambda f: (f.filed_at, f.accession))
    for filing in annual:
        for kind, wanted_type in ((RISKS, "risk"), (LITIGATION, None)):
            source = by_kind.get(kind)
            if source is None:
                continue
            in_section = [c for c in per_doc.get(filing.document_id, [])
                          if source.matches(section_title=c.section_title)
                          and (wanted_type is None or c.entity_type == wanted_type)]
            facts = _facts(kind, filing, in_section)
            if facts:
                label = f"{filing.fiscal_period} {filing.form_type}" if filing.fiscal_period \
                    else filing.form_type
                cards[kind].apply(as_of=filing.filed_at, document_id=str(filing.document_id),
                                  facts=facts, source_note=f"{label} · {filing.accession}")

    unavailable = {k: v for k, v in UNAVAILABLE.items() if k in cards}
    for kind in (RISKS, LITIGATION):
        if kind in cards and not cards[kind].revisions:
            n = len(annual)
            unavailable[kind] = (
                f"No annual filing on record has a {_SECTION_NAME[kind]} with extracted entities."
                if n else "No annual filings on record yet.")
    return ProjectedCards(cards=cards, unavailable=unavailable)
