"""Memory graph assembly.

Produces the frozen `/v1/company/{ticker}/graph` response:

    { "company": "NVDA",
      "nodes": [{ id, label, type, importance, mentions }],
      "edges": [{ source, target, relationship, strength }] }

Node ids are entity keys rather than database ids on purpose. A client caches
this graph and holds those ids; a surrogate key would change on a rebuild and
silently break every stored reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

from .importance import Signals, score_all
from .relationships import CO_OCCURS, Edge, TypedEdge, co_occurrence, degrees, strength, typed


@dataclass(slots=True)
class EntityInput:
    key: str
    label: str
    kind: str
    documents: set[str] = field(default_factory=set)
    mentions: int = 0
    first_seen_at: date | None = None
    last_seen_at: date | None = None


@dataclass(slots=True)
class Graph:
    company: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    def to_contract(self) -> dict[str, Any]:
        return {"company": self.company, "nodes": self.nodes, "edges": self.edges}


def build_graph(
    *,
    company: str,
    entities: Sequence[EntityInput],
    typed_edges: Sequence[TypedEdge] = (),
    total_documents: int | None = None,
    newest_filing: date | None = None,
    min_shared: int = 1,
    min_importance: int = 0,
    limit: int | None = None,
) -> Graph:
    known = {e.key for e in entities}
    mention_docs = {e.key: e.documents for e in entities}
    dates = {e.key: (e.first_seen_at, e.last_seen_at) for e in entities}

    edges = [e.normalised() for e in
             co_occurrence(mention_docs, min_shared=min_shared, dates=dates)]
    edges += typed(list(typed_edges), known=known)

    degree = degrees(edges)
    total_documents = total_documents or len(
        {d for e in entities for d in e.documents}) or 1
    newest_filing = newest_filing or max(
        (e.last_seen_at for e in entities if e.last_seen_at), default=None)

    scores = score_all(
        {e.key: Signals(mentions=e.mentions, documents=len(e.documents),
                        last_seen_at=e.last_seen_at, degree=degree.get(e.key, 0))
         for e in entities},
        total_documents=total_documents, newest_filing=newest_filing)

    nodes = [{
        "id": e.key,
        "label": e.label,
        "type": e.kind,
        "importance": scores[e.key].importance,
        "mentions": e.mentions,
    } for e in entities if scores[e.key].importance >= min_importance]

    nodes.sort(key=lambda n: (-n["importance"], n["id"]))
    if limit:
        nodes = nodes[:limit]

    kept = {n["id"] for n in nodes}
    max_weight = max((e.weight for e in edges), default=0)
    out_edges = [{
        "source": e.source,
        "target": e.target,
        "relationship": e.kind,
        "strength": strength(e, max_weight=max_weight),
    } for e in edges if e.source in kept and e.target in kept]
    out_edges.sort(key=lambda x: (-x["strength"], x["source"], x["target"]))

    return Graph(company=company, nodes=nodes, edges=out_edges)


def explain(entities: Sequence[EntityInput], key: str, *,
            total_documents: int | None = None,
            newest_filing: date | None = None,
            typed_edges: Sequence[TypedEdge] = ()) -> dict[str, Any]:
    """Why an entity scored what it did.

    The contract returns a bare number; this is how a reader gets behind it.
    A score nobody can interrogate is the same failure as an uncited claim.
    """
    graph = build_graph(company="", entities=entities, typed_edges=typed_edges,
                        total_documents=total_documents, newest_filing=newest_filing)
    node = next((n for n in graph.nodes if n["id"] == key), None)
    if node is None:
        return {}
    mention_docs = {e.key: e.documents for e in entities}
    dates = {e.key: (e.first_seen_at, e.last_seen_at) for e in entities}
    edges = [e.normalised() for e in co_occurrence(mention_docs, dates=dates)]
    edges += typed(list(typed_edges), known={e.key for e in entities})
    degree = degrees(edges)
    total_documents = total_documents or len(
        {d for e in entities for d in e.documents}) or 1
    newest = newest_filing or max(
        (e.last_seen_at for e in entities if e.last_seen_at), default=None)
    entity = next(e for e in entities if e.key == key)
    scores = score_all(
        {e.key: Signals(e.mentions, len(e.documents), e.last_seen_at,
                        degree.get(e.key, 0)) for e in entities},
        total_documents=total_documents, newest_filing=newest)
    return {"id": key, "importance": node["importance"],
            "components": scores[key].components,
            "signals": {"mentions": entity.mentions,
                        "documents": len(entity.documents),
                        "last_seen_at": entity.last_seen_at.isoformat()
                                        if entity.last_seen_at else None,
                        "degree": degree.get(key, 0)}}


def slice_by_period(graph: Graph, *, until: date,
                    entities: Sequence[EntityInput]) -> Graph:
    """The graph as it stood on a date — what a replay animation scrubs."""
    keep = {e.key for e in entities
            if e.first_seen_at is None or e.first_seen_at <= until}
    nodes = [n for n in graph.nodes if n["id"] in keep]
    ids = {n["id"] for n in nodes}
    return Graph(company=graph.company, nodes=nodes,
                 edges=[e for e in graph.edges
                        if e["source"] in ids and e["target"] in ids])
