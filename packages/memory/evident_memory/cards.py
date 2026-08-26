"""Memory cards — layer 3, the read model.

Cards are what a person reads; entities are what the system stores. A card is a
derived projection over layer 2, so rebuilding is deterministic and re-ingesting
a filing cannot duplicate it.

The defining property: a card is not a current value, it is an append-only
series of revisions — one per filing that touched it, each carrying a diff
against the one before.

    "CapEx: $14.6B"                                    <- a number
    "CapEx rose from $10.9B; attributed to data
     centres. Third increase in three years."          <- a card

The second only exists because the first revision was kept.

Routing binds at three different granularities, which is why it is a predicate
rather than a lookup:

    Revenue     form type      10-K, 10-Q
    Products    document kind  earnings_call
    Guidance    speaker role   CEO
    Risks       section        Item 1A
    CapEx       section        cash flow
    Litigation  section        Item 3
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from .entities import Evidence, Metric, Product, Promise, Risk, normalise_metric

REVENUE, PRODUCTS, GUIDANCE = "revenue", "products", "guidance"
RISKS, CAPEX, LITIGATION = "risks", "capex", "litigation"


# ----------------------------------------------------------------- routing
@dataclass(slots=True, frozen=True)
class CardSource:
    """A predicate over (document, section, speaker).

    A `None` field means "don't care". A populated one must match, so a rule can
    be as coarse as a form type or as narrow as one speaker inside one section.
    """
    card_kind: str
    source_label: str
    form_types: tuple[str, ...] | None = None
    doc_kinds: tuple[str, ...] | None = None
    section_pattern: str | None = None
    speaker_roles: tuple[str, ...] | None = None

    def matches(self, *, form_type: str | None = None, doc_kind: str | None = None,
                section_title: str | None = None, speaker_role: str | None = None) -> bool:
        if self.form_types is not None:
            if form_type is None or form_type not in self.form_types:
                return False
        if self.doc_kinds is not None:
            if doc_kind is None or doc_kind not in self.doc_kinds:
                return False
        if self.speaker_roles is not None:
            if speaker_role is None or speaker_role not in self.speaker_roles:
                return False
        if self.section_pattern is not None:
            if section_title is None or not re.search(self.section_pattern,
                                                      section_title, re.I):
                return False
        return True


# Mirrors the seed in sql/003_memory_cards.sql. A test asserts the two agree,
# because a silent drift here means a filing stops updating a card and nobody
# notices until the card is stale.
DEFAULT_SOURCES: tuple[CardSource, ...] = (
    CardSource(REVENUE,    "10-Q / 10-K",       form_types=("10-K", "10-Q")),
    CardSource(PRODUCTS,   "Earnings Call",     doc_kinds=("earnings_call",)),
    CardSource(GUIDANCE,   "CEO statements",    speaker_roles=("CEO",)),
    CardSource(RISKS,      "Risk section",      section_pattern=r"item\s*1a|risk\s*factors"),
    CardSource(CAPEX,      "Cash Flow section", section_pattern=r"cash\s*flow|capital\s*expend"),
    CardSource(LITIGATION, "Legal section",     section_pattern=r"item\s*3|legal\s*proceedings"),
)

CARD_TITLES = {REVENUE: "Revenue", PRODUCTS: "Products", GUIDANCE: "Guidance",
               RISKS: "Risks", CAPEX: "CapEx", LITIGATION: "Litigation"}


def route(*, form_type: str | None = None, doc_kind: str | None = None,
          section_title: str | None = None, speaker_role: str | None = None,
          sources: Sequence[CardSource] = DEFAULT_SOURCES) -> list[str]:
    """Which cards a piece of a filing updates. Order follows `sources`."""
    seen: list[str] = []
    for s in sources:
        if s.matches(form_type=form_type, doc_kind=doc_kind,
                     section_title=section_title, speaker_role=speaker_role):
            if s.card_kind not in seen:
                seen.append(s.card_kind)
    return seen


# ------------------------------------------------------------------- facts
@dataclass(slots=True, frozen=True)
class CardFact:
    """One line on a card. `key` is the identity used for diffing."""
    key: str
    label: str
    value: str | None = None
    unit: str | None = None
    period: str | None = None
    status: str | None = None
    evidence: Evidence | None = None

    def display(self) -> str:
        bits = [self.label]
        if self.value is not None:
            bits.append(f"{self.value}{' ' + self.unit if self.unit else ''}")
        if self.period:
            bits.append(f"({self.period})")
        if self.status:
            bits.append(f"[{self.status}]")
        return " ".join(bits)


@dataclass(slots=True)
class CardDelta:
    added: list[CardFact] = field(default_factory=list)
    removed: list[CardFact] = field(default_factory=list)
    changed: list[tuple[CardFact, CardFact]] = field(default_factory=list)

    @property
    def is_material(self) -> bool:
        """False when a filing touched the card but nothing actually moved.

        Lets the UI say "6 updates, 2 material" rather than implying every
        filing changed something.
        """
        return bool(self.added or self.removed or self.changed)

    def to_json(self) -> dict[str, Any]:
        return {
            "added":   [f.display() for f in self.added],
            "removed": [f.display() for f in self.removed],
            "changed": [{"label": b.label, "before": b.value, "after": a.value}
                        for b, a in self.changed],
        }


def diff_facts(previous: Sequence[CardFact], current: Sequence[CardFact]) -> CardDelta:
    prev = {f.key: f for f in previous}
    curr = {f.key: f for f in current}
    delta = CardDelta()
    for key, fact in curr.items():
        before = prev.get(key)
        if before is None:
            delta.added.append(fact)
        elif (before.value, before.status) != (fact.value, fact.status):
            delta.changed.append((before, fact))
    for key, fact in prev.items():
        if key not in curr:
            delta.removed.append(fact)
    return delta


# --------------------------------------------------------------- revisions
@dataclass(slots=True)
class CardRevision:
    revision: int
    as_of: date
    document_id: str
    facts: list[CardFact]
    delta: CardDelta
    summary: str
    source_note: str | None = None

    @property
    def is_material(self) -> bool:
        return self.delta.is_material

    @property
    def evidence(self) -> list[Evidence]:
        return [f.evidence for f in self.facts if f.evidence is not None]


@dataclass(slots=True)
class MemoryCard:
    kind: str
    title: str
    source_label: str
    revisions: list[CardRevision] = field(default_factory=list)

    @property
    def current(self) -> CardRevision | None:
        return self.revisions[-1] if self.revisions else None

    @property
    def history(self) -> list[CardRevision]:
        """Oldest first. The whole reason the card exists."""
        return list(self.revisions)

    @property
    def material_history(self) -> list[CardRevision]:
        return [r for r in self.revisions if r.is_material]

    def apply(self, *, as_of: date, document_id: str, facts: Sequence[CardFact],
              source_note: str | None = None) -> CardRevision | None:
        """Append a revision for one filing. Idempotent per document.

        Re-ingesting a filing must not add a second revision — the history is
        the product, and duplicating it would be worse than losing an update.
        """
        if any(r.document_id == document_id for r in self.revisions):
            return None
        previous = self.current.facts if self.current else []
        delta = diff_facts(previous, list(facts))
        revision = CardRevision(
            revision=len(self.revisions) + 1,
            as_of=as_of,
            document_id=document_id,
            facts=list(facts),
            delta=delta,
            summary=summarise(self.kind, delta, facts),
            source_note=source_note,
        )
        self.revisions.append(revision)
        return revision


def _fmt(value: str | None) -> str:
    return value if value is not None else "—"


def summarise(kind: str, delta: CardDelta, facts: Sequence[CardFact]) -> str:
    """A sentence a person would actually write about this revision."""
    if not delta.is_material:
        return "Restated without change."

    parts: list[str] = []
    for before, after in delta.changed:
        direction = _direction(before.value, after.value)
        parts.append(f"{before.label} {direction} from {_fmt(before.value)} "
                     f"to {_fmt(after.value)}")
    if delta.added:
        noun = "risk factor" if kind == RISKS else "item"
        parts.append(f"{len(delta.added)} new {noun}"
                     f"{'s' if len(delta.added) != 1 else ''}: "
                     + ", ".join(f.label for f in delta.added[:3]))
    if delta.removed:
        verb = "no longer disclosed" if kind in (RISKS, LITIGATION) else "dropped"
        parts.append(f"{len(delta.removed)} {verb}: "
                     + ", ".join(f.label for f in delta.removed[:3]))
    return "; ".join(parts) + "."


def _direction(before: str | None, after: str | None) -> str:
    try:
        b = float(str(before).replace(",", "").lstrip("$"))
        a = float(str(after).replace(",", "").lstrip("$"))
    except (TypeError, ValueError):
        return "changed"
    if a > b:
        return "rose"
    return "fell" if a < b else "held"


# -------------------------------------------------------------- projection
def _money(value: float | None) -> str | None:
    return None if value is None else f"{value:,.0f}"


def facts_for_metric(metrics: Iterable[Metric], wanted: str) -> list[CardFact]:
    """One fact per period — this is what makes a metric card a series."""
    target = normalise_metric(wanted)
    out: list[CardFact] = []
    for m in metrics:
        if m.normalised != target:
            continue
        for o in sorted(m.observations, key=lambda o: o.period):
            out.append(CardFact(
                key=f"{m.normalised}:{o.period}", label=f"{m.name} {o.period}",
                value=_money(o.value), unit=o.unit, period=o.period,
                status="restated" if o.is_restated else None,
                evidence=o.evidence,
            ))
    return out


def facts_for_products(products: Iterable[Product]) -> list[CardFact]:
    return [CardFact(key=f"product:{p.normalised}", label=p.name, status=p.status,
                     evidence=p.evidence[0] if p.evidence else None)
            for p in products]


def facts_for_risks(risks: Iterable[Risk]) -> list[CardFact]:
    # A dropped risk leaves the fact set, which surfaces as a `removed` in the
    # diff — that disappearance is the finding.
    return [CardFact(key=f"risk:{r.slug}", label=r.label, status=r.status,
                     evidence=r.evidence[0] if r.evidence else None)
            for r in risks if r.status == "active"]


def facts_for_guidance(promises: Iterable[Promise]) -> list[CardFact]:
    """Guidance is a promise. Same entity, read differently."""
    return [CardFact(key=f"promise:{p.statement[:60]}", label=p.statement,
                     status=p.status, period=p.horizon, evidence=p.made_evidence)
            for p in promises]


def build_cards(*, sources: Sequence[CardSource] = DEFAULT_SOURCES) -> dict[str, MemoryCard]:
    return {
        s.card_kind: MemoryCard(kind=s.card_kind,
                                title=CARD_TITLES.get(s.card_kind, s.card_kind.title()),
                                source_label=s.source_label)
        for s in sources
    }
