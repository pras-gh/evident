"""Cross-document resolution — where a pile of extractions becomes a memory.

Extraction runs per document and knows nothing about history. This module folds
those results into one entity per real-world thing, with a time axis:

  * the same metric under three different labels becomes one series
  * the same executive under four spellings becomes one person
  * a risk that stops being disclosed is marked dropped rather than deleted
  * a promise made in 2024 is carried forward until something settles it

On promises specifically: this module will never mark one `broken` on its own.
A company going quiet about a commitment is suggestive, not probative, and
asserting failure without evidence would be the same error as inventing a
filing quote. Silence past the horizon becomes `unclear`, which is surfaced —
an unresolved promise is itself the finding.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from .entities import (CompanyMemory, Evidence, Metric, Observation, Person,
                       Product, Promise, PromiseStatus, Risk, TimelineEntry,
                       Topic, normalise_metric)


def _extend(existing: date | None, candidate: date | None, *, latest: bool) -> date | None:
    if candidate is None:
        return existing
    if existing is None:
        return candidate
    return max(existing, candidate) if latest else min(existing, candidate)


def merge_topics(batches: Iterable[Sequence[Topic]]) -> list[Topic]:
    out: dict[str, Topic] = {}
    for batch in batches:
        for t in batch:
            cur = out.get(t.slug)
            if cur is None:
                out[t.slug] = Topic(slug=t.slug, label=t.label,
                                    first_seen_at=t.first_seen_at,
                                    last_seen_at=t.last_seen_at,
                                    evidence=list(t.evidence))
                continue
            cur.evidence.extend(t.evidence)
            cur.first_seen_at = _extend(cur.first_seen_at, t.first_seen_at, latest=False)
            cur.last_seen_at = _extend(cur.last_seen_at, t.last_seen_at, latest=True)
    return sorted(out.values(), key=lambda t: (-t.mention_count, t.slug))


def merge_people(batches: Iterable[Sequence[Person]]) -> list[Person]:
    out: dict[str, Person] = {}
    for batch in batches:
        for p in batch:
            cur = out.get(p.normalised)
            if cur is None:
                out[p.normalised] = Person(full_name=p.full_name, normalised=p.normalised,
                                           roles=list(p.roles),
                                           first_seen_at=p.first_seen_at,
                                           last_seen_at=p.last_seen_at,
                                           evidence=list(p.evidence))
                continue
            cur.evidence.extend(p.evidence)
            cur.roles.extend(r for r in p.roles if r not in cur.roles)
            cur.first_seen_at = _extend(cur.first_seen_at, p.first_seen_at, latest=False)
            cur.last_seen_at = _extend(cur.last_seen_at, p.last_seen_at, latest=True)
            # keep the longest spelling — usually the one with a middle name
            if len(p.full_name) > len(cur.full_name):
                cur.full_name = p.full_name
    return sorted(out.values(), key=lambda p: p.normalised)


def merge_products(batches: Iterable[Sequence[Product]]) -> list[Product]:
    out: dict[str, Product] = {}
    for batch in batches:
        for p in batch:
            cur = out.get(p.normalised)
            if cur is None:
                out[p.normalised] = Product(name=p.name, normalised=p.normalised,
                                            status=p.status,
                                            first_seen_at=p.first_seen_at,
                                            last_seen_at=p.last_seen_at,
                                            evidence=list(p.evidence))
                continue
            cur.evidence.extend(p.evidence)
            cur.first_seen_at = _extend(cur.first_seen_at, p.first_seen_at, latest=False)
            cur.last_seen_at = _extend(cur.last_seen_at, p.last_seen_at, latest=True)
    return sorted(out.values(), key=lambda p: p.normalised)


def merge_metrics(raw: Iterable[tuple[str, str | None, float | None, str | None, Evidence, date | None]]
                  ) -> list[Metric]:
    """Fold (name, period, value, unit, evidence, period_end) rows into series.

    A second observation of the same (metric, period) from a *later* document is
    a restatement, not a duplicate — companies revise. Both are kept, and the
    later one is flagged, because "they changed the number" is a finding.
    """
    out: dict[str, Metric] = {}
    for name, period, value, unit, evidence, period_end in raw:
        key = normalise_metric(name)
        metric = out.setdefault(key, Metric(name=name, normalised=key, unit=unit))
        restated = any(o.period == period for o in metric.observations)
        metric.observations.append(
            Observation(period=period or "unknown", value=value, unit=unit,
                        evidence=evidence, period_end=period_end,
                        is_restated=restated)
        )
    return sorted(out.values(), key=lambda m: m.normalised)


def merge_risks(batches: Iterable[Sequence[Risk]], *,
                latest_filing_date: date | None = None) -> list[Risk]:
    """Fold risks, and mark ones absent from the most recent filing as dropped.

    A risk factor quietly disappearing between two 10-Ks is one of the more
    informative things in the corpus, so it is a status change rather than a
    deletion — the history stays queryable.
    """
    out: dict[str, Risk] = {}
    for batch in batches:
        for r in batch:
            cur = out.get(r.slug)
            if cur is None:
                out[r.slug] = Risk(slug=r.slug, label=r.label, category=r.category,
                                   status=r.status, first_seen_at=r.first_seen_at,
                                   last_seen_at=r.last_seen_at,
                                   evidence=list(r.evidence))
                continue
            cur.evidence.extend(r.evidence)
            cur.first_seen_at = _extend(cur.first_seen_at, r.first_seen_at, latest=False)
            cur.last_seen_at = _extend(cur.last_seen_at, r.last_seen_at, latest=True)

    if latest_filing_date is not None:
        for r in out.values():
            if r.last_seen_at is not None and r.last_seen_at < latest_filing_date:
                r.status = "dropped"
    return sorted(out.values(), key=lambda r: r.slug)


# ----------------------------------------------------------------- promises
@dataclass(slots=True, frozen=True)
class ResolutionSignal:
    """Later evidence that settles an open promise.

    Produced by a second extraction pass over documents filed after the promise
    was made. Carrying evidence is mandatory — that is the whole point.
    """
    statement: str
    status: PromiseStatus
    evidence: Evidence
    resolved_at: date
    note: str | None = None


def resolve_promises(promises: Sequence[Promise],
                     signals: Sequence[ResolutionSignal],
                     *, as_of: date) -> list[Promise]:
    """Apply resolution signals, then mark unsettled past-due promises unclear.

    Never assigns `broken` without a signal that carries evidence.
    """
    by_statement: dict[str, Promise] = {p.statement: p for p in promises}

    for sig in signals:
        target = by_statement.get(sig.statement)
        if target is None or target.status != "open":
            continue
        if sig.status == "open":
            continue
        target.status = sig.status
        target.resolved_at = sig.resolved_at
        target.resolved_evidence = sig.evidence
        target.resolution_note = sig.note

    for p in promises:
        if p.is_overdue(as_of):
            p.status = "unclear"
            p.resolution_note = (
                "Past its stated horizon with nothing in later filings that "
                "settles it. Absence of disclosure is not evidence of failure."
            )
    return list(promises)


# ----------------------------------------------------------------- timeline
def build_timeline(memory: CompanyMemory) -> list[TimelineEntry]:
    """Flatten every dated entity into one ordered spine."""
    entries: list[TimelineEntry] = []

    for d in memory.documents:
        entries.append(TimelineEntry(occurred_at=d.filed_date, kind="filing",
                                     headline=f"{d.form_type} filed",
                                     ref=f"document:{d.document_id}"))
    for t in memory.topics:
        if t.first_seen_at:
            entries.append(TimelineEntry(occurred_at=t.first_seen_at, kind="topic",
                                         headline=f"{t.label} first appears",
                                         ref=f"topic:{t.slug}", topic_slug=t.slug,
                                         evidence=t.evidence[0] if t.evidence else None))
    for p in memory.products:
        if p.first_seen_at:
            entries.append(TimelineEntry(occurred_at=p.first_seen_at, kind="product",
                                         headline=f"{p.name} first mentioned",
                                         ref=f"product:{p.normalised}",
                                         evidence=p.evidence[0] if p.evidence else None))
    for r in memory.risks:
        if r.first_seen_at:
            entries.append(TimelineEntry(occurred_at=r.first_seen_at, kind="risk",
                                         headline=f"Risk disclosed: {r.label}",
                                         ref=f"risk:{r.slug}",
                                         evidence=r.evidence[0] if r.evidence else None))
        if r.status == "dropped" and r.last_seen_at:
            entries.append(TimelineEntry(occurred_at=r.last_seen_at, kind="risk",
                                         headline=f"Risk no longer disclosed: {r.label}",
                                         ref=f"risk:{r.slug}:dropped"))
    for pr in memory.promises:
        entries.append(TimelineEntry(occurred_at=pr.made_at, kind="promise",
                                     headline=f"Committed: {pr.statement}",
                                     ref=f"promise:{pr.statement[:40]}",
                                     topic_slug=pr.topic_slug,
                                     evidence=pr.made_evidence))
        if pr.resolved_at:
            entries.append(TimelineEntry(occurred_at=pr.resolved_at, kind="promise",
                                         headline=f"{pr.status.title()}: {pr.statement}",
                                         ref=f"promise:{pr.statement[:40]}:resolved",
                                         evidence=pr.resolved_evidence))
    for m in memory.metrics:
        for o in m.observations:
            if o.period_end:
                entries.append(TimelineEntry(
                    occurred_at=o.period_end, kind="metric",
                    headline=f"{m.name} {o.period}: {o.value}{' ' + o.unit if o.unit else ''}",
                    ref=f"metric:{m.normalised}:{o.period}", evidence=o.evidence))
    for e in memory.events:
        entries.append(TimelineEntry(occurred_at=e.occurred_at, kind="event",
                                     headline=e.headline, ref=f"event:{e.kind}",
                                     evidence=e.evidence))

    return sorted(entries, key=lambda e: (e.occurred_at, e.kind, e.ref))
