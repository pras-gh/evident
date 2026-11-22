"""Relationship builder.

Edges come from two places.

**Co-occurrence** is derived: two entities mentioned in the same filing are
related, weighted by how many filings they share. It is cheap, always available,
and honest — the edge can name the documents that produced it.

**Typed edges** come from extraction: `blackwell -> drives_investment ->
ai_infrastructure`. These say something co-occurrence cannot, and they are the
ones a reader actually wants. They are also the ones a model can invent, so an
extracted edge is only kept when both endpoints already exist as entities;
an edge to something we never saw mentioned is dropped rather than stored.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

CO_OCCURS = "mentioned_with"


@dataclass(slots=True, frozen=True)
class Edge:
    source: str
    target: str
    kind: str
    weight: int = 1
    documents: tuple[str, ...] = ()
    first_seen_at: date | None = None
    last_seen_at: date | None = None

    def normalised(self) -> "Edge":
        """Undirected kinds are stored one way round so A-B and B-A are one edge."""
        if self.kind == CO_OCCURS and self.source > self.target:
            return Edge(self.target, self.source, self.kind, self.weight,
                        self.documents, self.first_seen_at, self.last_seen_at)
        return self


@dataclass(slots=True)
class TypedEdge:
    """An edge asserted by extraction rather than derived."""
    source_key: str
    target_key: str
    relationship: str
    document_id: str
    confidence: float | None = None
    observed_at: date | None = None


def co_occurrence(mentions: dict[str, set[str]], *, min_shared: int = 1,
                  dates: dict[str, tuple[date | None, date | None]] | None = None
                  ) -> list[Edge]:
    """`mentions` maps entity key -> the document ids it appears in."""
    dates = dates or {}
    keys = sorted(mentions)
    out: list[Edge] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = mentions[a] & mentions[b]
            if len(shared) < min_shared:
                continue
            fa, la = dates.get(a, (None, None))
            fb, lb = dates.get(b, (None, None))
            out.append(Edge(a, b, CO_OCCURS, len(shared), tuple(sorted(shared)),
                            _min(fa, fb), _max(la, lb)))
    return out


def typed(edges: list[TypedEdge], *, known: set[str]) -> list[Edge]:
    """Fold extracted edges, dropping any endpoint we never saw as an entity."""
    grouped: dict[tuple[str, str, str], list[TypedEdge]] = defaultdict(list)
    for e in edges:
        if e.source_key not in known or e.target_key not in known:
            continue                      # an edge to nothing is not an edge
        if e.source_key == e.target_key:
            continue
        grouped[(e.source_key, e.target_key, e.relationship)].append(e)

    out: list[Edge] = []
    for (src, dst, rel), group in grouped.items():
        docs = tuple(sorted({e.document_id for e in group}))
        seen = [e.observed_at for e in group if e.observed_at]
        out.append(Edge(src, dst, rel, len(docs), docs,
                        min(seen) if seen else None, max(seen) if seen else None))
    return out


def strength(edge: Edge, *, max_weight: int) -> float:
    """0-1, relative to the strongest edge in this company's graph.

    Corpus-relative for the same reason importance is: an absolute count means
    nothing to a reader and shifts as filings accumulate.
    """
    if max_weight <= 0:
        return 0.0
    return round(min(1.0, edge.weight / max_weight), 4)


def degrees(edges: list[Edge]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for e in edges:
        out[e.source] += 1
        out[e.target] += 1
    return dict(out)


def _min(a, b):
    return min([d for d in (a, b) if d], default=None)


def _max(a, b):
    return max([d for d in (a, b) if d], default=None)
