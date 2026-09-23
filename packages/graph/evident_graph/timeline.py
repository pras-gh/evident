"""Timeline: what changed between a company's filings, and where it says so.

Events come from comparing each filing with the previous filing **of the same
form** — never from dates alone. An entity's `first_seen` is the first filing
Evident *ingested* that names it, not the first filing the company wrote about
it; a timeline built from it would announce everything in the oldest loaded
filing as new. See docs/timeline-engine.md.

Pure: no database, no clock. The API loads filings and mentions and calls
`build_timeline`; tests call it with literals.

The unit of measure is the **paragraph**. A topic cited in 20 paragraphs of a
filing, up from 13 in the last one, has been expanded. Paragraphs are counted
by base id, so an oversized paragraph the chunker split on sentence boundaries
(`16_4`, `16_4#2`) counts once — counting the parts would let the chunker, not
the company, move the number. A paragraph that runs over a page break still
counts twice (the parser sees two blocks, `16_5` and `17_1`), which is one
reason small differences are not reported: see `MIN_DELTA`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal, Sequence

Kind = Literal["newly_disclosed", "disclosed_again", "expanded", "narrowed",
               "no_longer_disclosed", "filed"]

#: Forms whose content is comparable filing to filing. An 8-K reports one event,
#: so "this 8-K does not mention China, the last one did" means nothing.
PERIODIC_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F"})

#: Forms that restate the full picture each time, so absence is informative.
#: A 10-Q's risk factors usually describe only changes since the 10-K; an
#: entity missing from one has not been dropped.
ANNUAL_FORMS = frozenset({"10-K", "20-F", "40-F"})

#: Expanded means cited in at least this many more paragraphs than last time…
#: Three, not two: re-paginating a paragraph across a page break, or splitting
#: one bullet into two, moves a count by one or two without the company having
#: said anything new.
MIN_DELTA = 3
#: …and at least this many times as many. Both, so 1 → 2 (double, but one
#: paragraph) and 40 → 43 (three more, but noise) are not events. Narrowed is
#: the mirror image. These were chosen after looking at three years of NVIDIA
#: Risk Factors, and are tuned to that — see the docs before trusting them on
#: a filer that writes very differently.
MIN_RATIO = 1.25

#: A paragraph is **carried over** when at least this share of its four-word
#: phrases already appear in what the compared filing said about the same
#: topic. Two simpler tests fail on real filings:
#:
#: * exact text — NVIDIA lightly edits nearly every paragraph each year
#:   ("…or tariffs; and" becomes "…or tariffs;"); only 2 of FY2026's 26
#:   export-control paragraphs are verbatim from FY2025;
#: * paragraph-to-paragraph similarity — a page break cuts paragraphs at
#:   different points each year, and the fragment "and financial results.
#:   Export controls targeting GPUs…" scores low against the whole paragraph
#:   it was cut from, so it reads as new when it is not.
#:
#: Phrase overlap against everything the old filing said on the topic handles
#: both. On the NVIDIA corpus it is sharply two-sided — see
#: docs/timeline-engine.md — and like MIN_DELTA it was set after looking.
CARRIED_OVER = 0.5
SHINGLE = 4

#: Display order within one filing, most informative first.
KIND_ORDER: dict[str, int] = {
    "filed": 0, "newly_disclosed": 1, "expanded": 2, "narrowed": 3,
    "no_longer_disclosed": 4, "disclosed_again": 5,
}

_TITLE: dict[str, str] = {
    "newly_disclosed": "{name} Newly Disclosed",
    "disclosed_again": "{name} Disclosed Again",
    "expanded": "{name} Expanded",
    "narrowed": "{name} Narrowed",
    "no_longer_disclosed": "{name} No Longer Disclosed",
}


@dataclass(frozen=True, slots=True)
class Filing:
    document_id: int
    accession: str
    form_type: str
    filed_at: date
    fiscal_period: str | None = None
    page_count: int | None = None

    @property
    def label(self) -> str:
        return f"{self.fiscal_period} {self.form_type}" if self.fiscal_period else self.form_type

    @property
    def amended(self) -> bool:
        return self.form_type.endswith("/A")


@dataclass(frozen=True, slots=True)
class Topic:
    entity_id: int
    slug: str
    name: str
    entity_type: str


@dataclass(frozen=True, slots=True)
class Mention:
    entity_id: int
    document_id: int
    chunk_id: int | None
    paragraph_id: str | None
    page: int | None
    quote: str | None = None
    confidence: float | None = None
    #: the paragraph's full text, when the caller has it; used to prefer
    #: evidence that is new in this filing over a paragraph carried forward
    text: str | None = None
    #: stands in for a paragraph id when a mention has neither a paragraph
    #: nor a chunk, so it still counts once
    mention_id: int | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    document_id: int
    chunk_id: int | None
    paragraph_id: str | None
    page: int | None
    quote: str | None
    confidence: float | None
    #: True when most of this paragraph is not in what the compared filing
    #: said about the topic (see CARRIED_OVER); None when there was nothing to
    #: compare, or no text to compare with
    new_paragraph: bool | None


@dataclass(frozen=True, slots=True)
class Event:
    kind: Kind
    filing: Filing
    #: the same-form filing this one was compared with; None for `filed`
    compared_with: Filing | None = None
    topic: Topic | None = None
    #: paragraphs citing the topic in `filing` and in `compared_with`
    paragraphs: int | None = None
    previous_paragraphs: int | None = None
    evidence: Evidence | None = None
    #: for `newly_disclosed`: forms that named the topic before this one did,
    #: e.g. a 10-Q ahead of the first 10-K that mentions it
    named_earlier_in: tuple[str, ...] = ()

    @property
    def date(self) -> date:
        return self.filing.filed_at

    @property
    def category(self) -> str:
        return self.topic.entity_type.title() if self.topic else "Filing"

    @property
    def title(self) -> str:
        if self.topic is None:
            return f"{self.filing.label} Filed"
        return _TITLE[self.kind].format(name=self.topic.name)

    @property
    def summary(self) -> str:
        if self.topic is None:
            d = self.filing.filed_at
            pages = f", {self.filing.page_count} pages" if self.filing.page_count else ""
            return f"Form {self.filing.form_type} filed {d:%b} {d.day}, {d.year}{pages}."
        was = self.compared_with.label if self.compared_with else "the last filing"
        n, before = self.paragraphs or 0, self.previous_paragraphs or 0
        if self.kind == "newly_disclosed":
            if self.named_earlier_in:
                return (f"Cited in {_paras(n)}; first time in a {self.filing.form_type}, "
                        f"named earlier in a {' and a '.join(self.named_earlier_in)}.")
            return f"Cited in {_paras(n)}; in no earlier filing on record."
        if self.kind == "disclosed_again":
            return (f"Cited in {_paras(n)}; absent from the {was}, "
                    f"cited in an earlier {self.filing.form_type}.")
        if self.kind == "expanded":
            return f"Cited in {_paras(n)}, up from {before} in the {was}."
        if self.kind == "narrowed":
            return f"Cited in {_paras(n)}, down from {before} in the {was}."
        return f"Cited in {_paras(before)} of the {was}; in none of this filing."

    @property
    def key(self) -> str:
        """Stable across requests and rebuilds — the accession and the slug,
        not database ids."""
        return f"{self.filing.accession}:{self.kind}:{self.topic.slug if self.topic else ''}"


@dataclass(slots=True)
class Timeline:
    events: list[Event] = field(default_factory=list)
    #: accession → "baseline" | "compared" | "not compared"
    coverage: dict[str, str] = field(default_factory=dict)
    filings: list[Filing] = field(default_factory=list)


def _paras(n: int) -> str:
    return f"{n} paragraph{'' if n == 1 else 's'}"


def base_paragraph(paragraph_id: str) -> str:
    """`16_4#2` → `16_4`: the sentence-split parts of one paragraph are one
    paragraph."""
    return paragraph_id.split("#", 1)[0]


_POSITION = re.compile(r"^(\d+)_(\d+)(?:#(\d+))?$")


def _reading_order(m: Mention) -> tuple:
    """Where a mention sits in its filing, for picking the first one.

    Positional paragraph ids (`{page}_{index}`) sort by page then index; the
    chunk id breaks ties and orders anything without a positional id, since
    chunks are numbered in document order.
    """
    hit = _POSITION.match(m.paragraph_id or "")
    if hit:
        page, index, part = hit.groups()
        return (0, int(page), int(index), int(part or 0), m.chunk_id or 0)
    return (1, m.page or 0, 0, 0, m.chunk_id or 0)


def _paragraph_key(m: Mention) -> str:
    if m.paragraph_id:
        return base_paragraph(m.paragraph_id)
    if m.chunk_id is not None:
        return f"chunk:{m.chunk_id}"
    return f"mention:{m.mention_id}"


_WORD = re.compile(r"\w+")


def shingles(text: str) -> set[tuple[str, ...]]:
    """Every run of SHINGLE consecutive words, case-folded. Order within a
    paragraph is kept; where a paragraph starts or ends is not."""
    words = _WORD.findall(text.lower())
    if len(words) <= SHINGLE:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def overlap(text: str, earlier: set[tuple[str, ...]]) -> float:
    """The share of `text`'s phrases found in `earlier`: 1.0 when all of it was
    said before, 0.0 when none of it was."""
    mine = shingles(text)
    return len(mine & earlier) / len(mine) if mine else 1.0


def expanded(before: int, after: int) -> bool:
    return before > 0 and after - before >= MIN_DELTA and after >= MIN_RATIO * before


def narrowed(before: int, after: int) -> bool:
    return after > 0 and before - after >= MIN_DELTA and before >= MIN_RATIO * after


def build_timeline(filings: Sequence[Filing], topics: Iterable[Topic],
                   mentions: Iterable[Mention]) -> Timeline:
    """Every event across `filings`, newest filing first.

    The earliest filing of each form is a **baseline**: it gets a `filed` event
    and nothing else, because nothing in it can be new relative to a filing
    that is not loaded. Amended and non-periodic filings are listed but never
    compared; an amendment often restates one Part of a report, and comparing it
    with a complete one would report most of the report as dropped.
    """
    topic_by_id = {t.entity_id: t for t in topics}
    ordered = sorted(filings, key=lambda f: (f.filed_at, f.accession))
    known = {f.document_id for f in ordered}

    # document → entity → mentions, in reading order
    cited: dict[int, dict[int, list[Mention]]] = {}
    for m in mentions:
        if m.document_id in known and m.entity_id in topic_by_id:
            cited.setdefault(m.document_id, {}).setdefault(m.entity_id, []).append(m)
    for by_entity in cited.values():
        for ms in by_entity.values():
            ms.sort(key=_reading_order)

    def count(document_id: int, entity_id: int) -> int:
        return len({_paragraph_key(m) for m in cited.get(document_id, {}).get(entity_id, [])})

    timeline = Timeline(filings=list(ordered))
    previous_of_form: dict[str, Filing] = {}
    # entity → forms of the earlier compared filings that cite it
    seen_in: dict[int, set[str]] = {}

    for filing in ordered:
        timeline.events.append(Event(kind="filed", filing=filing))
        comparable = filing.form_type in PERIODIC_FORMS and not filing.amended
        if not comparable:
            timeline.coverage[filing.accession] = "not compared"
            continue

        here = cited.get(filing.document_id, {})
        prev = previous_of_form.get(filing.form_type)
        if prev is None:
            timeline.coverage[filing.accession] = "baseline"
        else:
            timeline.coverage[filing.accession] = "compared"
            there = cited.get(prev.document_id, {})
            for entity_id in sorted(set(here) | set(there)):
                timeline.events.extend(_compare(
                    filing, prev, topic_by_id[entity_id],
                    after=count(filing.document_id, entity_id),
                    before=count(prev.document_id, entity_id),
                    here=here.get(entity_id, []), there=there.get(entity_id, []),
                    seen_in=seen_in.get(entity_id, set())))

        previous_of_form[filing.form_type] = filing
        for entity_id in here:
            seen_in.setdefault(entity_id, set()).add(filing.form_type)

    timeline.events.sort(key=lambda e: (
        -e.filing.filed_at.toordinal(), e.filing.accession, KIND_ORDER[e.kind],
        -abs((e.paragraphs or 0) - (e.previous_paragraphs or 0)),
        e.topic.name.lower() if e.topic else ""))
    return timeline


def _compare(filing: Filing, prev: Filing, topic: Topic, *, after: int, before: int,
             here: list[Mention], there: list[Mention],
             seen_in: set[str]) -> list[Event]:
    common = dict(filing=filing, compared_with=prev, topic=topic,
                  paragraphs=after, previous_paragraphs=before)
    if after and not before:
        if filing.form_type in seen_in:
            # in an older filing of this form, missing from the last one
            return [Event(kind="disclosed_again", evidence=_evidence(here, there),
                          **common)]
        return [Event(kind="newly_disclosed", evidence=_evidence(here, there),
                      named_earlier_in=tuple(sorted(seen_in)), **common)]
    if before and not after:
        if filing.form_type not in ANNUAL_FORMS:
            return []
        # Nothing in this filing to point at, so point at where it last was.
        return [Event(kind="no_longer_disclosed", evidence=_evidence(there, None), **common)]
    if expanded(before, after):
        return [Event(kind="expanded", evidence=_evidence(here, there), **common)]
    if narrowed(before, after):
        return [Event(kind="narrowed", evidence=_evidence(here, there), **common)]
    return []


def _evidence(mentions: list[Mention], earlier: list[Mention] | None) -> Evidence | None:
    """The first paragraph that shows the change.

    A paragraph carried over from last year says nothing about what changed, so
    the first one that is not carried over is preferred. Without text to
    compare, the first mention wins and its newness is left unknown.
    """
    if not mentions:
        return None
    pick, new = mentions[0], None
    if earlier is not None and all(m.text for m in mentions):
        old: set[tuple[str, ...]] = set()
        for text in {m.text for m in earlier if m.text}:
            old |= shingles(text)
        fresh = next((m for m in mentions if overlap(m.text, old) < CARRIED_OVER), None)
        pick, new = (fresh, True) if fresh else (mentions[0], False)
    return Evidence(document_id=pick.document_id, chunk_id=pick.chunk_id,
                    paragraph_id=pick.paragraph_id, page=pick.page, quote=pick.quote,
                    confidence=pick.confidence, new_paragraph=new)
