"""The company memory model.

    CompanyMemory {
      companyId, ticker, documents[], timeline[], topics[], people[],
      metrics[], risks[], promises[], products[], events[]
    }

Every entity carries `evidence` — the paragraphs that assert it. That is not
decoration. An entity without evidence cannot be cited, and an entity that
cannot be cited has no business in this product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

PromiseStatus = Literal["open", "kept", "broken", "abandoned", "unclear"]
RiskStatus = Literal["active", "dropped"]
ProductStatus = Literal["mentioned", "announced", "shipping", "discontinued"]

_PUNCT = re.compile(r"[^a-z0-9]+")
_HONORIFIC = re.compile(r"^(mr|mrs|ms|dr|prof)\.?\s+", re.I)
_SUFFIX = re.compile(r"[,\s]+(jr|sr|ii|iii|iv|phd|md)\.?$", re.I)


def slugify(text: str) -> str:
    return _PUNCT.sub("-", text.strip().casefold()).strip("-")


def normalise_person(name: str) -> str:
    """Fold the variations a filing uses for one human.

    'Mr. Jensen Huang', 'Jensen Huang', 'JENSEN HUANG' and 'Huang, Jensen' all
    appear in the same corpus and are the same person.
    """
    n = _SUFFIX.sub("", _HONORIFIC.sub("", name.strip()))
    if "," in n:
        last, _, first = n.partition(",")
        n = f"{first.strip()} {last.strip()}"
    return _PUNCT.sub(" ", n.casefold()).strip()


def normalise_metric(name: str) -> str:
    """Fold label drift so a metric stays one series across years.

    'Capital expenditures', 'Capital Expenditure' and 'capex' are the same line.
    """
    n = _PUNCT.sub(" ", name.casefold()).strip()
    aliases = {
        "capex": "capital expenditures",
        "capital expenditure": "capital expenditures",
        "r d expense": "research and development expense",
        "rd expense": "research and development expense",
        "revenues": "revenue",
        "net revenue": "revenue",
        "total revenue": "revenue",
    }
    return aliases.get(n, n)


@dataclass(slots=True, frozen=True)
class Evidence:
    """A span of a filing that supports exactly one claim."""
    document_id: str
    paragraph_id: str | None
    page_number: int | None
    quote: str
    table_id: str | None = None
    observed_at: date | None = None


@dataclass(slots=True)
class DocumentRef:
    document_id: str
    accession: str
    form_type: str
    filed_date: date
    published_at: str
    fiscal_period: str | None = None
    page_count: int | None = None


@dataclass(slots=True)
class Topic:
    slug: str
    label: str
    first_seen_at: date | None = None
    last_seen_at: date | None = None
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def mention_count(self) -> int:
        return len(self.evidence)


@dataclass(slots=True)
class Role:
    role: str
    from_date: date | None = None
    to_date: date | None = None


@dataclass(slots=True)
class Person:
    full_name: str
    normalised: str
    roles: list[Role] = field(default_factory=list)
    first_seen_at: date | None = None
    last_seen_at: date | None = None
    evidence: list[Evidence] = field(default_factory=list)

    def role_on(self, when: date) -> str | None:
        """Who they were at a point in time — 'the CFO' is date-dependent."""
        for r in self.roles:
            if (r.from_date is None or r.from_date <= when) and \
               (r.to_date is None or when <= r.to_date):
                return r.role
        return None


@dataclass(slots=True)
class Observation:
    period: str
    value: float | None
    unit: str | None
    evidence: Evidence
    period_end: date | None = None
    is_restated: bool = False


@dataclass(slots=True)
class Metric:
    name: str
    normalised: str
    unit: str | None = None
    observations: list[Observation] = field(default_factory=list)

    def series(self) -> list[tuple[str, float | None]]:
        return [(o.period, o.value) for o in
                sorted(self.observations, key=lambda o: (o.period_end or date.min, o.period))]


@dataclass(slots=True)
class Risk:
    slug: str
    label: str
    category: str | None = None
    status: RiskStatus = "active"
    first_seen_at: date | None = None
    last_seen_at: date | None = None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(slots=True)
class Promise:
    """A forward-looking statement, tracked until something settles it."""
    statement: str
    made_at: date
    made_evidence: Evidence
    horizon: str | None = None
    due_date: date | None = None
    topic_slug: str | None = None
    status: PromiseStatus = "open"
    resolved_at: date | None = None
    resolved_evidence: Evidence | None = None
    resolution_note: str | None = None

    def is_overdue(self, as_of: date) -> bool:
        return (self.status == "open" and self.due_date is not None
                and self.due_date < as_of)


@dataclass(slots=True)
class Product:
    name: str
    normalised: str
    status: ProductStatus = "mentioned"
    first_seen_at: date | None = None
    last_seen_at: date | None = None
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(slots=True)
class Event:
    kind: str
    headline: str
    occurred_at: date
    evidence: Evidence


@dataclass(slots=True)
class TimelineEntry:
    occurred_at: date
    kind: str
    headline: str
    ref: str
    topic_slug: str | None = None
    evidence: Evidence | None = None


@dataclass(slots=True)
class CompanyMemory:
    company_id: str
    ticker: str | None = None
    documents: list[DocumentRef] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)
    promises: list[Promise] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "documents": len(self.documents), "timeline": len(self.timeline),
            "topics": len(self.topics), "people": len(self.people),
            "metrics": len(self.metrics), "risks": len(self.risks),
            "promises": len(self.promises), "products": len(self.products),
            "events": len(self.events),
        }

    def open_promises(self, as_of: date) -> list[Promise]:
        return [p for p in self.promises if p.status == "open"]

    def overdue_promises(self, as_of: date) -> list[Promise]:
        """Promises past their horizon that nothing has settled.

        Deliberately not called 'broken'. See resolve.py — silence is not
        evidence of failure, but it is worth surfacing.
        """
        return [p for p in self.promises if p.is_overdue(as_of)]
